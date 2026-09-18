"""Embedding-backed memory using pgvector.

pgvector is written directly against SQLAlchemy rather than through a
framework: the package has no dependencies of its own, so nothing here can
conflict with FastAPI or Pydantic.

Two details that are easy to get wrong:
  * Use `pgvector.sqlalchemy.VECTOR` and do NOT call `register_vector`. That
    helper is for raw asyncpg pools and is explicitly untested on the async
    engine.
  * `text-embedding-004` was retired in January 2026. `gemini-embedding-001`
    below 3072 dimensions does not normalise for you, so we do it ourselves.

Any failure falls back to the keyword index over the same documents, flagged
`degraded` so the dashboard can say so.
"""

from __future__ import annotations

import asyncio
import logging
import math

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.database import utcnow
from ..database.models import MemoryDocument
from .service import LocalIndexMemory, MemoryHit, merchant_history

logger = logging.getLogger(__name__)


def l2_normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        return vector
    return [v / norm for v in vector]


class PgVectorMemory:
    name = "pgvector"

    def __init__(self, settings: Settings, fallback: LocalIndexMemory) -> None:
        self.settings = settings
        self.fallback = fallback
        self._client = None

    def _genai(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.settings.gemini_api_key)
        return self._client

    async def embed(self, content: str, *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
        client = self._genai()
        response = await asyncio.wait_for(
            client.aio.models.embed_content(
                model=self.settings.embedding_model,
                contents=content,
                config={
                    "task_type": task_type,
                    "output_dimensionality": self.settings.embedding_dimensions,
                },
            ),
            timeout=self.settings.memory_timeout_seconds,
        )
        values = list(response.embeddings[0].values)
        return l2_normalise(values)

    async def ensure_column(self, session: AsyncSession) -> None:
        """Add the vector column on first use, so no migration is needed."""
        await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await session.execute(
            text(
                "ALTER TABLE memory_documents ADD COLUMN IF NOT EXISTS "
                f"embedding vector({self.settings.embedding_dimensions})"
            )
        )
        await session.commit()

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
        started = utcnow()
        try:
            await self.ensure_column(session)
            vector = await self.embed(text, task_type="RETRIEVAL_QUERY")
            literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"

            from sqlalchemy import text as sql

            rows = await session.execute(
                sql(
                    """
                    SELECT id, kind, ref_id, merchant_id, title, body, header,
                           1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM memory_documents
                    WHERE embedding IS NOT NULL
                      AND (:exclude IS NULL OR ref_id IS DISTINCT FROM :exclude)
                    ORDER BY embedding <=> CAST(:vec AS vector)
                    LIMIT :limit
                    """
                ),
                {"vec": literal, "exclude": exclude_case_id, "limit": self.settings.memory_top_k * 2},
            )
            hits: list[MemoryHit] = []
            for row in rows:
                header = row.header or {}
                score = float(row.similarity or 0)
                if merchant_id and row.merchant_id == merchant_id:
                    score *= 1.15
                hits.append(
                    MemoryHit(
                        kind=row.kind,
                        doc_id=row.id,
                        case_id=header.get("CASE_ID"),
                        title=row.title,
                        summary=(header.get("SUMMARY") or "")[:300],
                        diagnosis=header.get("DIAGNOSIS"),
                        resolution=header.get("OUTCOME"),
                        resolution_time_seconds=header.get("RESOLUTION_TIME_SECONDS"),
                        merchant_id=row.merchant_id,
                        similarity=min(score, 0.97),
                        source=self.name,
                    )
                )

            if not hits:
                raise RuntimeError("no embedded documents yet")

            hits.sort(key=lambda h: h.similarity, reverse=True)
            cases = [h for h in hits if h.kind == "case"][: self.settings.memory_top_k]
            knowledge = [h for h in hits if h.kind == "knowledge"][:3]
            return {
                "query": text,
                "similar_cases": [h.as_dict() for h in cases],
                "knowledge": [h.as_dict() for h in knowledge],
                "merchant_history": await merchant_history(session, merchant_id),
                "provider": self.name,
                "degraded": False,
                "latency_ms": int((utcnow() - started).total_seconds() * 1000),
            }
        except Exception as exc:  # noqa: BLE001 - memory must never block a case
            logger.warning("pgvector search failed (%s); falling back to the keyword index", exc)
            result = await self.fallback.search(
                session,
                text=text,
                merchant_id=merchant_id,
                transaction_id=transaction_id,
                exclude_case_id=exclude_case_id,
                intent_hint=intent_hint,
            )
            result["degraded"] = True
            return result

    async def remember_case(self, session: AsyncSession, case_id: str) -> str | None:
        doc_id = await self.fallback.remember_case(session, case_id)
        if doc_id:
            await self._embed_document(session, doc_id)
        return doc_id

    async def add_knowledge(
        self, session: AsyncSession, *, title: str, content: str, tags: list[str]
    ) -> str:
        doc_id = await self.fallback.add_knowledge(
            session, title=title, content=content, tags=tags
        )
        await self._embed_document(session, doc_id)
        return doc_id

    async def _embed_document(self, session: AsyncSession, doc_id: str) -> None:
        try:
            await self.ensure_column(session)
            doc = await session.get(MemoryDocument, doc_id)
            if doc is None:
                return
            vector = await self.embed(f"{doc.title}\n{doc.body}")
            literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
            from sqlalchemy import text as sql

            await session.execute(
                sql(
                    "UPDATE memory_documents SET embedding = CAST(:vec AS vector), "
                    "embedded_at = now() WHERE id = :id"
                ),
                {"vec": literal, "id": doc_id},
            )
            await session.flush()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not embed %s (%s); the keyword index still covers it", doc_id, exc)

    async def backfill(self, session: AsyncSession) -> int:
        """Embed anything the keyword index already holds."""
        rows = await session.scalars(
            select(MemoryDocument.id).where(MemoryDocument.embedded_at.is_(None))
        )
        count = 0
        for doc_id in rows:
            await self._embed_document(session, doc_id)
            count += 1
        await session.commit()
        return count
