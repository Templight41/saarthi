"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .api import cases, escalations, internal, merchants, scenarios, simulation, voice
from .api.deps import get_runtime, get_session
from .config import get_settings
from .database.database import init_db
from .database.models import Merchant
from .database.seed import seed_all
from .memory.knowledge import seed_knowledge
from .metrics.service import compute_metrics
from .runtime import SaarthiRuntime
from .services.errors import EnterpriseAPIError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    # Refuse to start on stand-ins. Discovering that mid-demo is worse than
    # not starting at all.
    problems = settings.validate_providers()
    if problems:
        raise RuntimeError(
            "Refusing to start on simulated providers:\n  - "
            + "\n  - ".join(problems)
            + "\n\nFix the configuration, or set ALLOW_SIMULATED=true to permit them."
        )

    runtime = SaarthiRuntime.build(settings)
    logger.info("Providers: %s", runtime.health())
    app.state.runtime = runtime

    await init_db(runtime.engine)
    prepare = getattr(runtime.memory, "ensure_schema", None)
    if prepare is not None:
        await prepare(runtime.session_factory)
    warm = getattr(runtime.memory, "warm", None)
    if warm is not None:
        await warm()
    if settings.seed_on_startup:
        async with runtime.session_factory() as session:
            existing = await session.scalar(select(func.count()).select_from(Merchant))
            if not existing:
                await seed_all(session)
                logger.info("Seeded demo data")
            await seed_knowledge(session, runtime.memory)
            await session.commit()

    yield

    await runtime.runner.shutdown()
    if runtime.workflows is not None:
        await runtime.workflows.shutdown()
    await runtime.engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Saarthi",
        description="Autonomous merchant operations teammate",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # allow_headers governs the request; reading a custom header off a
        # response needs this. Without it the voice metadata is invisible to
        # the browser as soon as the dashboard stops proxying through Vite.
        expose_headers=[
            "X-Saarthi-Voice-Provider",
            "X-Saarthi-Voice-Model",
            "X-Saarthi-Voice-Language",
            "X-Saarthi-Voice-Latency-Ms",
            "X-Saarthi-Voice-Simulated",
            "X-Saarthi-Voice-Cache",
        ],
    )

    @app.exception_handler(EnterpriseAPIError)
    async def _enterprise_error(request, exc: EnterpriseAPIError):
        return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})

    app.include_router(cases.router)
    app.include_router(escalations.router)
    app.include_router(merchants.router)
    app.include_router(scenarios.router)
    app.include_router(simulation.router)
    app.include_router(voice.router)
    app.include_router(internal.router)

    misc = APIRouter(tags=["system"])

    @misc.get("/api/health")
    async def health(runtime: SaarthiRuntime = Depends(get_runtime)) -> dict:
        info = runtime.health()
        if runtime.workflows is not None:
            info["workflows"] = {**info["workflows"], **await runtime.workflows.health()}
        return info

    @misc.get("/api/metrics")
    async def metrics(
        session: AsyncSession = Depends(get_session),
        runtime: SaarthiRuntime = Depends(get_runtime),
    ) -> dict:
        return await compute_metrics(session, runtime.settings)

    app.include_router(misc)
    return app


app = create_app()
