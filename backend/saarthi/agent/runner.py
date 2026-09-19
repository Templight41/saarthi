"""Background execution of the agent loop.

Deliberately not FastAPI BackgroundTasks: those run inside the request's
dependency scope, so the request session would be closed underneath the loop,
and they are neither cancellable nor awaitable from a test.

Task references are held, because a bare `create_task` result can be garbage
collected mid-flight. A crashed run writes an event so a stuck case is visible
on the timeline rather than silently frozen.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import Settings

logger = logging.getLogger(__name__)


class CaseRunner:
    def __init__(self, supervisor, settings: Settings) -> None:
        self.supervisor = supervisor
        self.settings = settings
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, case_id: str) -> asyncio.Lock:
        if case_id not in self._locks:
            self._locks[case_id] = asyncio.Lock()
        return self._locks[case_id]

    @property
    def inline(self) -> bool:
        return self.settings.agent_run_mode == "inline"

    async def start(self, case_id: str, *, trigger: str = "NEW_MESSAGE") -> None:
        if self.inline:
            async with self._lock(case_id):
                await self.supervisor.run_case(case_id, trigger=trigger)
            return
        await self._spawn(case_id, self._run(case_id, trigger))

    async def resume(self, case_id: str, *, trigger: str) -> None:
        if self.inline:
            async with self._lock(case_id):
                await self.supervisor.resume_case(case_id, trigger=trigger)
            return
        await self._spawn(case_id, self._resume(case_id, trigger))

    async def follow_up(self, case_id: str) -> None:
        """Answer a merchant question on a case that is already open.

        Takes the same per-case lock as the agent loop, so the answer lands
        after the current step rather than racing it — two sessions both
        bumping `case.next_sequence` would otherwise interleave the audit
        trail.
        """
        if self.inline:
            async with self._lock(case_id):
                await self.supervisor.answer_follow_up(case_id)
            return
        await self._spawn(case_id, self._follow_up(case_id))

    async def _follow_up(self, case_id: str) -> None:
        async with self._lock(case_id):
            await self.supervisor.answer_follow_up(case_id)

    async def _spawn(self, case_id: str, coro) -> None:
        existing = self._tasks.get(case_id)
        if existing is not None and not existing.done():
            # Let the running loop finish its step; the lock serialises us.
            pass
        task = asyncio.create_task(coro, name=f"saarthi-case-{case_id}")
        self._tasks[case_id] = task
        task.add_done_callback(lambda t: self._on_done(case_id, t))

    async def _run(self, case_id: str, trigger: str) -> None:
        async with self._lock(case_id):
            await self.supervisor.run_case(case_id, trigger=trigger)

    async def _resume(self, case_id: str, trigger: str) -> None:
        async with self._lock(case_id):
            await self.supervisor.resume_case(case_id, trigger=trigger)

    def _on_done(self, case_id: str, task: asyncio.Task) -> None:
        self._tasks.pop(case_id, None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception("Case %s failed in the background", case_id, exc_info=exc)

    async def wait_for(self, case_id: str, timeout: float = 15.0) -> None:
        task = self._tasks.get(case_id)
        if task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            logger.warning("Timed out waiting for case %s", case_id)

    async def cancel(self, case_id: str) -> None:
        task = self._tasks.pop(case_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
