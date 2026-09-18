"""Semantic memory.

Postgres remains the transactional truth. This layer only supplies advisory
historical context, and the supervisor treats it that way: memory informs the
prompt, it never overrides current state.

Two backends share one document corpus, so the fallback is a real search over
the same content rather than a stub. `LocalIndexMemory` is TF-IDF with domain
synonyms and structured boosts; `PgVectorMemory` adds embeddings when a key is
available.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.database import utcnow
from ..database.enums import CaseStatus
from ..database.ids import next_id
from ..database.models import Case, MemoryDocument

logger = logging.getLogger(__name__)

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do", "does", "for",
    "from", "had", "has", "have", "he", "her", "his", "i", "if", "in", "into", "is", "it", "its",
    "me", "my", "of", "on", "or", "our", "she", "so", "that", "the", "their", "them", "then",
    "there", "they", "this", "to", "was", "we", "were", "what", "when", "which", "who", "why",
    "will", "with", "you", "your", "please", "hi", "hello",
}

# Merchants do not use our vocabulary. Map theirs onto ours on both sides.
SYNONYMS = {
    "deducted": "debited",
    "charged": "debited",
    "taken": "debited",
    "stuck": "delayed",
    "late": "delayed",
    "payout": "settlement",
    "settle": "settlement",
    "settled": "settlement",
    "quality": "product_quality",
    "defect": "product_quality",
    "defects": "product_quality",
    "damaged": "product_quality",
    "faulty": "product_quality",
    "cancelled": "refund",
    "cancel": "refund",
    "returned": "refund",
    "reversal": "refund",
}

TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.lower()):
        if raw in STOPWORDS or len(raw) < 2:
            continue
        tokens.append(SYNONYMS.get(raw, raw))
    return tokens


@dataclass
class MemoryHit:
    kind: str
    doc_id: str
    case_id: str | None
    title: str
    summary: str
    diagnosis: str | None
    resolution: str | None
    resolution_time_seconds: int | None
    merchant_id: str | None
    similarity: float
    source: str

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "doc_id": self.doc_id,
            "case_id": self.case_id,
            "title": self.title,
            "summary": self.summary,
            "diagnosis": self.diagnosis,
            "resolution": self.resolution,
            "resolution_time_seconds": self.resolution_time_seconds,
            "merchant_id": self.merchant_id,
            "similarity": round(self.similarity, 3),
            "source": self.source,
        }


class SaarthiMemory(Protocol):
    name: str

    async def search(
        self,
        session: AsyncSession,
        *,
        text: str,
        merchant_id: str | None = None,
        transaction_id: str | None = None,
        intent_hint: str | None = None,
        exclude_case_id: str | None = None,
    ) -> dict: ...

    async def remember_case(self, session: AsyncSession, case_id: str) -> str | None: ...

    async def add_knowledge(
        self, session: AsyncSession, *, title: str, content: str, tags: list[str]
    ) -> str: ...


class LocalIndexMemory:
    """TF-IDF over the shared document corpus, with structured boosts."""

    name = "local_index"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def _corpus(self, session: AsyncSession) -> list[MemoryDocument]:
        rows = await session.scalars(select(MemoryDocument))
        return list(rows)

    async def search(
        self,
        session: AsyncSession,
        *,
        text: str,
        merchant_id: str | None = None,
        transaction_id: str | None = None,
        exclude_case_id: str | None = None,
        intent_hint: str | None = None,
    ) -> dict:
        docs = await self._corpus(session)
        # A case is never its own precedent.
        if exclude_case_id:
            docs = [d for d in docs if d.ref_id != exclude_case_id]
        started = utcnow()
        hits: list[MemoryHit] = []

        if docs:
            query_tokens = tokenize(text)
            query_counts = Counter(query_tokens)
            doc_tokens = {d.id: tokenize(f"{d.title} {d.body}") for d in docs}
            df: Counter = Counter()
            for tokens in doc_tokens.values():
                df.update(set(tokens))
            total = len(docs)

            def vector(counts: Counter) -> dict[str, float]:
                out: dict[str, float] = {}
                for token, count in counts.items():
                    idf = math.log((total + 1) / (df.get(token, 0) + 1)) + 1.0
                    out[token] = (1 + math.log(count)) * idf
                return out

            qv = vector(query_counts)
            qnorm = math.sqrt(sum(v * v for v in qv.values())) or 1.0

            for doc in docs:
                dv = vector(Counter(doc_tokens[doc.id]))
                dnorm = math.sqrt(sum(v * v for v in dv.values())) or 1.0
                dot = sum(qv.get(t, 0.0) * dv.get(t, 0.0) for t in qv)
                score = dot / (qnorm * dnorm)

                header = doc.header or {}
                if merchant_id and doc.merchant_id == merchant_id:
                    score *= 1.25
                if intent_hint and header.get("INTENT") == intent_hint:
                    score *= 1.5
                if transaction_id and transaction_id in (doc.body or ""):
                    score *= 2.0
                tags = set(doc.tags or [])
                if tags & set(tokenize(text)):
                    score *= 1.15

                if score < 0.05:
                    continue
                hits.append(
                    MemoryHit(
                        kind=doc.kind,
                        doc_id=doc.id,
                        case_id=header.get("CASE_ID"),
                        title=doc.title,
                        summary=header.get("SUMMARY", "")[:300],
                        diagnosis=header.get("DIAGNOSIS"),
                        resolution=header.get("OUTCOME"),
                        resolution_time_seconds=_maybe_int(header.get("RESOLUTION_TIME_SECONDS")),
                        merchant_id=doc.merchant_id,
                        # Never claim a perfect match.
                        similarity=min(score, 0.97),
                        source=self.name,
                    )
                )

        hits.sort(key=lambda h: h.similarity, reverse=True)
        top_k = self.settings.memory_top_k
        cases = [h for h in hits if h.kind == "case"][:top_k]
        knowledge = [h for h in hits if h.kind == "knowledge"][:3]

        history = await merchant_history(session, merchant_id) if merchant_id else None
        return {
            "query": text,
            "similar_cases": [h.as_dict() for h in cases],
            "knowledge": [h.as_dict() for h in knowledge],
            "merchant_history": history,
            "provider": self.name,
            "degraded": False,
            "latency_ms": int((utcnow() - started).total_seconds() * 1000),
        }

    async def remember_case(self, session: AsyncSession, case_id: str) -> str | None:
        return await ingest_case_document(session, case_id)

    async def add_knowledge(
        self, session: AsyncSession, *, title: str, content: str, tags: list[str]
    ) -> str:
        doc = MemoryDocument(
            id=await next_id(session, "memory"),
            kind="knowledge",
            title=title,
            body=content,
            header={"KIND": "knowledge", "TITLE": title},
            tags=tags,
        )
        session.add(doc)
        await session.flush()
        return doc.id


async def merchant_history(session: AsyncSession, merchant_id: str | None) -> dict | None:
    """Always from Postgres. Memory search never supplies this."""
    if not merchant_id:
        return None
    rows = await session.scalars(
        select(Case).where(Case.merchant_id == merchant_id, Case.status == CaseStatus.RESOLVED)
    )
    cases = list(rows)
    by_intent: dict[str, int] = {}
    for case in cases:
        if case.intent:
            by_intent[case.intent] = by_intent.get(case.intent, 0) + 1
    last = max((c.resolved_at for c in cases if c.resolved_at), default=None)
    return {
        "merchant_id": merchant_id,
        "previous_case_count": len(cases),
        "by_intent": by_intent,
        "last_case_at": last.isoformat() if last else None,
    }


async def ingest_case_document(session: AsyncSession, case_id: str) -> str | None:
    """Build and persist the memory document for a resolved case."""
    case = await session.get(Case, case_id)
    if case is None:
        return None

    existing = await session.scalar(
        select(MemoryDocument).where(MemoryDocument.ref_id == case_id, MemoryDocument.kind == "case")
    )
    if existing is not None:
        return existing.id

    diagnosis = case.diagnosis or {}
    duration = None
    if case.resolved_at and case.created_at:
        duration = int((case.resolved_at - case.created_at).total_seconds())

    header = {
        "KIND": "case",
        "CASE_ID": case.id,
        "MERCHANT_ID": case.merchant_id,
        "TRANSACTION_ID": case.transaction_id,
        "INTENT": case.intent,
        "DIAGNOSIS": diagnosis.get("root_cause"),
        "RISK": case.risk.value if case.risk else None,
        "RESOLUTION_TYPE": case.resolution.value if case.resolution else None,
        "RESOLUTION_TIME_SECONDS": duration,
        "SUMMARY": diagnosis.get("summary") or case.original_message,
        "OCCURRED_AT": case.created_at.isoformat(),
    }

    from ..agent.events import list_events

    events = await list_events(session, case.id)
    actions = [e.message for e in events if e.event_type in {"ACTION_COMPLETED", "REFUND_SCHEDULED"}]
    failures = [e.message for e in events if e.event_type == "ACTION_FAILED"]
    recovery = [e.message for e in events if e.event_type == "RECOVERY_DECIDED"]

    tags = _tags_for(case, diagnosis)
    body = "\n".join(
        [
            f"COMPLAINT: {case.original_message}",
            f"DIAGNOSIS: {diagnosis.get('summary', '')}",
            f"ACTIONS: {'; '.join(actions) if actions else 'none'}",
            f"FAILURES: {'; '.join(failures) if failures else 'none'}",
            f"RECOVERY: {'; '.join(recovery) if recovery else 'none'}",
            f"OUTCOME: resolved as {case.resolution.value if case.resolution else 'unknown'}"
            + (f" in {duration}s" if duration else ""),
        ]
    )

    doc = MemoryDocument(
        id=await next_id(session, "memory"),
        kind="case",
        ref_id=case.id,
        merchant_id=case.merchant_id,
        title=f"{_humanise(diagnosis.get('root_cause', 'case'))} — {case.transaction_id}",
        body=body,
        header=header,
        tags=tags,
    )
    session.add(doc)
    await session.flush()
    return doc.id


def _tags_for(case: Case, diagnosis: dict) -> list[str]:
    tags = []
    if case.intent:
        tags.append(case.intent.lower())
    root = diagnosis.get("root_cause")
    if root:
        tags.append(root.lower())
    return tags


def _humanise(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _maybe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_memory(settings: Settings) -> SaarthiMemory:
    # pgvector is used when embeddings are available; the local index is the
    # default and is always the fallback.
    if settings.memory_provider == "pgvector" and settings.gemini_api_key:
        from .pgvector_memory import PgVectorMemory

        return PgVectorMemory(settings, LocalIndexMemory(settings))
    if settings.memory_provider == "auto" and settings.gemini_api_key and not settings.is_sqlite:
        from .pgvector_memory import PgVectorMemory

        return PgVectorMemory(settings, LocalIndexMemory(settings))
    return LocalIndexMemory(settings)
