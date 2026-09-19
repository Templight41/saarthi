from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from fastapi.responses import JSONResponse
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
        # The developer's .env must not reach the test suite. Without this a
        # machine with a real SARVAM_API_KEY silently builds real providers and
        # bills real calls, because allow_simulated *permits* stand-ins rather
        # than forcing them. Pinning one field at a time only works until the
        # next field is added, so the file is switched off wholesale.
        _env_file=None,
        # Tests must be deterministic, offline and free. This is the one place
        # stand-ins are legitimate, and it is opted into explicitly.
        allow_simulated=True,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        llm_provider="mock",
        memory_provider="local",
        workflow_engine="local",
        voice_provider="mock",
        # Belt and braces: os.environ still wins over _env_file, so an exported
        # key in CI would otherwise reach the synthesizer.
        sarvam_api_key="",
        gemini_api_key="",
        google_cloud_project="",
        tts_provider="mock",
        sarvam_tts_model="bulbul:v3",
        sarvam_tts_speaker="shubh",
        tts_max_characters=400,
        tts_sample_rate=22050,
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


@pytest_asyncio.fixture
async def client(settings, session_factory):
    """An app assembled from the real routers.

    Hand-built rather than `create_app()` so it can share the test session
    factory, but it registers the same EnterpriseAPIError handler — otherwise a
    not_found raised in a service is a clean 404 in production and an unhandled
    exception here.
    """
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from saarthi.api import cases, escalations, scenarios, simulation, voice
    from saarthi.api.deps import get_session
    from saarthi.main import create_app
    from saarthi.memory.knowledge import seed_knowledge
    from saarthi.metrics.service import compute_metrics
    from saarthi.runtime import SaarthiRuntime
    from saarthi.services.errors import EnterpriseAPIError

    runtime = SaarthiRuntime.build(settings, session_factory=session_factory)
    async with session_factory() as db:
        await seed_all(db)
        await seed_knowledge(db, runtime.memory)
        await db.commit()

    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(cases.router)
    app.include_router(escalations.router)
    app.include_router(scenarios.router)
    app.include_router(simulation.router)
    app.include_router(voice.router)

    @app.exception_handler(EnterpriseAPIError)
    async def _enterprise_error(request, exc: EnterpriseAPIError):
        return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})

    @app.get("/api/metrics")
    async def metrics():
        async with session_factory() as db:
            return await compute_metrics(db, settings)

    @app.get("/api/health")
    async def health():
        return runtime.health()

    _ = create_app, get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.runtime = runtime
        yield c


@pytest.fixture(autouse=True)
def reset_simulation():
    simulation_state.reset()
    yield
    simulation_state.reset()


@pytest.fixture(autouse=True)
def reset_audio_cache():
    """Message ids restart with the fixtures, so audio must not outlive a test."""
    from saarthi.api.voice import _audio_cache

    _audio_cache.clear()
    yield
    _audio_cache.clear()
