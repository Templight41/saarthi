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

    def __init__(self, settings: Settings, fallback: LocalIndexMemory, session_factory=None) -> None:
        self.settings = settings
        self.fallback = fallback
        self.session_factory = session_factory
        self._client = None
        self._ready = False

    def _genai(self):
        if self._client is None:
            # Same builder as the chat provider, so embeddings and generation
            # always go to the same backend.
            from ..llm.gemini import build_genai_client

            self._client = build_genai_client(self.settings)
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

    async def embed_many(self, contents: list[str]) -> list[list[float]]:
        """One API call for many documents. Seeding embeds 11 at once."""
        if not contents:
            return []
        client = self._genai()
        response = await asyncio.wait_for(
            client.aio.models.embed_content(
                model=self.settings.embedding_model,
                contents=contents,
                config={
                    "task_type": "RETRIEVAL_DOCUMENT",
                    "output_dimensionality": self.settings.embedding_dimensions,
                },
            ),
            timeout=max(self.settings.memory_timeout_seconds, 30.0),
        )
        return [l2_normalise(list(e.values)) for e in response.embeddings]

    def bind(self, session_factory) -> None:
        self.session_factory = session_factory

    async def ensure_schema(self, session_factory) -> None:
        self.session_factory = self.session_factory or session_factory
        """Create the vector column once, at startup, in its own session.

        Doing this inside a caller's session would commit their in-flight work
        early, and a DDL failure would poison their transaction.
        """
        async with session_factory() as session:
            await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await session.execute(
                text(
                    "ALTER TABLE memory_documents ADD COLUMN IF NOT EXISTS "
                    f"embedding vector({self.settings.embedding_dimensions})"
                )
            )
            await session.commit()
        self._ready = True

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
            vector = await self.embed(text, task_type="RETRIEVAL_QUERY")
            literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"

            from sqlalchemy import text as sql

            # Deliberately not the caller's session.
            owned = self.session_factory() if self.session_factory else None
            query_session = owned or session
            rows = await query_session.execute(
                sql(
                    """
                    SELECT id, kind, ref_id, merchant_id, title, body, header,
                           1 - (embedding <=> CAST(:vec AS vector)) AS similarity
                    FROM memory_documents
                    WHERE embedding IS NOT NULL
                      AND (CAST(:exclude AS text) IS NULL
                           OR ref_id IS DISTINCT FROM CAST(:exclude AS text))
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

            if owned is not None:
                await owned.close()

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
            logger.warning(
                "pgvector search failed (%s: %s); falling back to the keyword index",
                type(exc).__name__, str(exc)[:200],
            )
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
        # Left unembedded on purpose: seeding writes many of these, and one
        # batched backfill is far faster than an API call per document.
        return await self.fallback.add_knowledge(
            session, title=title, content=content, tags=tags
        )

    async def _embed_document(self, session: AsyncSession, doc_id: str) -> None:
        try:
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
            logger.warning(
                "Could not embed %s (%s: %s); the keyword index still covers it",
                doc_id, type(exc).__name__, exc,
            )

    async def backfill(self, session: AsyncSession) -> int:
        """Embed every document that has no vector yet, in one batched call."""
        rows = list(
            await session.execute(
                select(MemoryDocument.id, MemoryDocument.title, MemoryDocument.body).where(
                    MemoryDocument.embedded_at.is_(None)
                )
            )
        )
        if not rows:
            return 0
        try:
            vectors = await self.embed_many([f"{r.title}\n{r.body}" for r in rows])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Batch embedding failed (%s: %s); the keyword index still covers these",
                type(exc).__name__, exc,
            )
            return 0

        from sqlalchemy import text as sql

        for row, vector in zip(rows, vectors, strict=False):
            literal = "[" + ",".join(f"{v:.6f}" for v in vector) + "]"
            await session.execute(
                sql(
                    "UPDATE memory_documents SET embedding = CAST(:vec AS vector), "
                    "embedded_at = now() WHERE id = :id"
                ),
                {"vec": literal, "id": row.id},
            )
        await session.commit()
        logger.info("Embedded %d memory documents", len(rows))
        return len(rows)
