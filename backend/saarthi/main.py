"""FastAPI application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .api import cases, escalations, internal, simulation, voice
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

    runtime = SaarthiRuntime.build(settings)
    app.state.runtime = runtime

    await init_db(runtime.engine)
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
    )

    @app.exception_handler(EnterpriseAPIError)
    async def _enterprise_error(request, exc: EnterpriseAPIError):
        return JSONResponse(status_code=exc.status, content={"error": exc.as_dict()})

    app.include_router(cases.router)
    app.include_router(escalations.router)
    app.include_router(simulation.router)
    app.include_router(voice.router)
    app.include_router(internal.router)

    misc = APIRouter(tags=["system"])

    @misc.get("/api/health")
    async def health(runtime: SaarthiRuntime = Depends(get_runtime)) -> dict:
        info = runtime.health()
        if runtime.workflows is not None:
            info["workflows"] = await runtime.workflows.health()
        info["voice"] = runtime.settings.voice_provider
        return info

    @misc.get("/api/metrics")
    async def metrics(
        session: AsyncSession = Depends(get_session),
        runtime: SaarthiRuntime = Depends(get_runtime),
    ) -> dict:
        return await compute_metrics(session, runtime.settings)

    @misc.get("/api/merchants")
    async def merchants(session: AsyncSession = Depends(get_session)) -> dict:
        rows = await session.scalars(select(Merchant))
        return {
            "merchants": [
                {
                    "id": m.id,
                    "name": m.name,
                    "risk_level": m.risk_level.value,
                    "autonomous_refund_limit": str(m.autonomous_refund_limit),
                }
                for m in rows
            ]
        }

    app.include_router(misc)
    return app


app = create_app()
