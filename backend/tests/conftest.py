from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from saarthi.config import Settings
from saarthi.database.database import init_db, make_engine, make_session_factory
from saarthi.database.seed import seed_all
from saarthi.simulation.failure_injection import simulation_state


@pytest.fixture
def settings(tmp_path) -> Settings:
    # A file-backed SQLite DB, not :memory: — each pooled aiosqlite connection
    # would otherwise get its own private database.
    return Settings(
        # Tests must be deterministic, offline and free. This is the one place
        # stand-ins are legitimate, and it is opted into explicitly.
        allow_simulated=True,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        llm_provider="mock",
        memory_provider="local",
        workflow_engine="local",
        voice_provider="mock",
        agent_run_mode="inline",
        agent_step_delay_seconds=0.0,
        seed_on_startup=False,
    )


@pytest_asyncio.fixture
async def session_factory(settings: Settings) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = make_engine(settings)
    await init_db(engine, drop=True)
    yield make_session_factory(engine)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as s:
        yield s


@pytest_asyncio.fixture
async def seeded(session: AsyncSession) -> AsyncSession:
    await seed_all(session)
    return session


@pytest.fixture(autouse=True)
def reset_simulation():
    simulation_state.reset()
    yield
    simulation_state.reset()
