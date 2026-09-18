"""Workflow engines.

n8n schedules and waits; the backend owns every decision and every side effect.
Both engines call the *same* step functions, so the fallback is genuinely the
same business logic rather than a parallel implementation that drifts. The only
difference is who does the waiting, and the UI shows which one is live.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.enums import Actor, WorkflowStatus
from ..database.ids import next_id
from ..database.models import Case, WorkflowRun

logger = logging.getLogger(__name__)

WORKFLOWS = {
    "case_memory_ingestion",
    "scheduled_refund",
    "settlement_monitor",
    "failure_recovery",
    "human_approval",
}


class BaseWorkflowEngine:
    name = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runner = None
        self.session_factory = None
        self._tasks: set[asyncio.Task] = set()

    def bind(self, runner, session_factory, settings: Settings) -> None:
        self.runner = runner
        self.session_factory = session_factory
        self.settings = settings

    async def start(
        self, session: AsyncSession, workflow: str, payload: dict, *, case: Case | None = None
    ) -> WorkflowRun:
        run = WorkflowRun(
            id=await next_id(session, "workflow"),
            workflow=workflow,
            engine=self.name,
            case_id=case.id if case else payload.get("case_id"),
            status=WorkflowStatus.DISPATCHED,
            payload=payload,
            state={},
        )
        session.add(run)
        await session.flush()

        if case is not None:
            from ..agent.events import EventType, record_event

            await record_event(
                session,
                case,
                EventType.WORKFLOW_STARTED,
                actor=Actor.N8N if self.name == "n8n" else Actor.WORKFLOW,
                message=f"Workflow {workflow.replace('_', ' ')} started ({self.name})",
                result={"run_id": run.id, "workflow": workflow, "engine": self.name},
            )

        await self._dispatch(run, payload)
        return run

    async def _dispatch(self, run: WorkflowRun, payload: dict) -> None:  # pragma: no cover
        raise NotImplementedError

    def _track(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def health(self) -> dict:
        return {"engine": self.name, "reachable": True}


class LocalWorkflowEngine(BaseWorkflowEngine):
    """In-process loops. The default, so the demo never depends on n8n."""

    name = "local"

    async def _dispatch(self, run: WorkflowRun, payload: dict) -> None:
        run_id, workflow = run.id, run.workflow
        if workflow == "case_memory_ingestion":
            self._track(self._ingest_memory(run_id, payload["case_id"]))
        elif workflow == "scheduled_refund":
            self._track(self._scheduled_refund_loop(run_id, payload["case_id"]))
        elif workflow == "human_approval":
            # Nothing to drive locally: the dashboard is the approval surface.
            run.status = WorkflowStatus.WAITING
        elif workflow == "settlement_monitor":
            self._track(self._settlement_scan(run_id))

    async def _ingest_memory(self, run_id: str, case_id: str) -> None:
        if self.session_factory is None:
            return
        from .steps import memory_ingest

        try:
            async with self.session_factory() as session:
                await memory_ingest(session, run_id, case_id, actor=Actor.WORKFLOW)
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Memory ingestion failed for %s", case_id)

    async def _scheduled_refund_loop(self, run_id: str, case_id: str) -> None:
        if self.session_factory is None:
            return
        from .steps import (
            scheduled_refund_check,
            scheduled_refund_complete,
            scheduled_refund_execute,
        )

        wait = self.settings.scheduled_refund_wait_seconds
        max_checks = self.settings.scheduled_refund_max_checks

        for check in range(1, max_checks + 1):
            await asyncio.sleep(wait)
            try:
                async with self.session_factory() as session:
                    state = await scheduled_refund_check(session, run_id, case_id, check=check)
                    await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("Scheduled refund check failed for %s", case_id)
                return

            if state["settlement_status"] == "COMPLETED":
                async with self.session_factory() as session:
                    await scheduled_refund_complete(session, run_id, case_id)
                    await session.commit()
                if self.runner is not None:
                    await self.runner.resume(case_id, trigger="SETTLEMENT_UPDATE")
                return

            if state["threshold_reached"] or check == max_checks:
                async with self.session_factory() as session:
                    await scheduled_refund_execute(session, run_id, case_id)
                    await session.commit()
                if self.runner is not None:
                    await self.runner.resume(case_id, trigger="SCHEDULED_REFUND_DUE")
                return

    async def _settlement_scan(self, run_id: str) -> None:
        if self.session_factory is None:
            return
        from .steps import settlement_scan

        try:
            async with self.session_factory() as session:
                await settlement_scan(session, run_id, self.settings, runner=self.runner)
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Settlement scan failed")

    async def run_now(self, session: AsyncSession, workflow: str) -> WorkflowRun:
        return await self.start(session, workflow, {})


class N8nWorkflowEngine(BaseWorkflowEngine):
    """Fires webhooks at n8n. Falls back per run, not globally."""

    name = "n8n"

    WEBHOOK_PATHS = {
        "case_memory_ingestion": "saarthi/case-resolved",
        "scheduled_refund": "saarthi/scheduled-refund",
        "settlement_monitor": "saarthi/settlement-monitor/run-now",
        "failure_recovery": "saarthi/action-failed",
        "human_approval": "saarthi/escalation-created",
    }

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self._local = LocalWorkflowEngine(settings)

    def bind(self, runner, session_factory, settings: Settings) -> None:
        super().bind(runner, session_factory, settings)
        self._local.bind(runner, session_factory, settings)

    async def _dispatch(self, run: WorkflowRun, payload: dict) -> None:
        path = self.WEBHOOK_PATHS.get(run.workflow)
        if path is None:
            return
        url = f"{self.settings.n8n_webhook_url.rstrip('/')}/{path}"
        body = {
            "run_id": run.id,
            **payload,
            "wait_seconds": self.settings.scheduled_refund_wait_seconds,
            "threshold_seconds": self.settings.scheduled_refund_threshold_seconds,
        }
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.post(url, json=body)
                response.raise_for_status()
            run.status = WorkflowStatus.RUNNING
        except Exception as exc:  # noqa: BLE001
            logger.warning("n8n dispatch failed for %s (%s); running locally instead", run.id, exc)
            run.engine = "local"
            run.state = {**(run.state or {}), "fallback_reason": str(exc)}
            await self._local._dispatch(run, payload)

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{self.settings.n8n_base_url.rstrip('/')}/healthz")
                return {"engine": self.name, "reachable": response.status_code < 500}
        except Exception:  # noqa: BLE001
            return {"engine": self.name, "reachable": False}

    async def run_now(self, session: AsyncSession, workflow: str) -> WorkflowRun:
        return await self.start(session, workflow, {})


def build_workflow_engine(settings: Settings) -> BaseWorkflowEngine:
    if settings.workflow_engine == "n8n":
        return N8nWorkflowEngine(settings)
    if settings.workflow_engine == "local":
        return LocalWorkflowEngine(settings)
    # auto: prefer local, because an unreachable n8n should never be discovered
    # for the first time during a demo.
    return LocalWorkflowEngine(settings)


async def list_runs(session: AsyncSession, case_id: str | None = None) -> list[WorkflowRun]:
    stmt = select(WorkflowRun).order_by(WorkflowRun.created_at.desc())
    if case_id:
        stmt = stmt.where(WorkflowRun.case_id == case_id)
    rows = await session.scalars(stmt)
    return list(rows)


async def get_run(session: AsyncSession, run_id: str) -> WorkflowRun | None:
    return await session.get(WorkflowRun, run_id)


def _unused(value: Any) -> None:  # pragma: no cover
    return None
