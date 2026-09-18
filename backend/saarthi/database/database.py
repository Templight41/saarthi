"""Engine, session factory and portable column types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, TypeDecorator, event
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from ..config import Settings


class Base(DeclarativeBase):
    pass


# JSONB on Postgres, plain JSON on SQLite.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def portable_enum(enum_cls: type) -> SAEnum:
    """Store enums as their string values, never as native DB enum types.

    Native Postgres enums make reseeding painful and do not exist in SQLite.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=40,
        values_callable=lambda e: [m.value for m in e],
        validate_strings=True,
    )


class TZDateTime(TypeDecorator):
    """Always store UTC; reattach tzinfo on the way out.

    SQLite drops timezone information, which otherwise produces naive/aware
    comparison errors deep inside the agent loop.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_engine(settings: Settings) -> AsyncEngine:
    if settings.is_sqlite:
        engine = create_async_engine(
            settings.database_url,
            connect_args={"timeout": 30},
            future=True,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_conn.cursor()
            # WAL lets the background agent loop and timeline polls overlap.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine

    return create_async_engine(settings.database_url, pool_size=5, max_overflow=5, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False is mandatory: handlers read ORM objects after commit.
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db(engine: AsyncEngine, *, drop: bool = False) -> None:
    from . import models  # noqa: F401  (ensure models are registered)

    async with engine.begin() as conn:
        if not engine.url.drivername.startswith("sqlite"):
            from sqlalchemy import text

            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        if drop:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def session_dependency(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
