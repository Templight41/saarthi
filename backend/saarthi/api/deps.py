from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ..runtime import SaarthiRuntime


def get_runtime(request: Request) -> SaarthiRuntime:
    return request.app.state.runtime


async def get_session(
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> AsyncIterator[AsyncSession]:
    async with runtime.session_factory() as session:
        yield session
