"""Workflow step functions.

Both engines call these. An n8n HTTP node reaches them through the internal
API; the local engine calls them directly. Identical logic either way.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import EventType, record_event
from ..config import Settings
from ..database.database import utcnow
from ..database.enums import Actor, CaseOrigin, CaseStatus, EventStatus, WorkflowStatus
from ..database.ids import next_id
from ..database.models import Case, ProactiveAlert, WorkflowRun
from ..memory.service import ingest_case_document
from ..services import ledger_service, refund_service

logger = logging.getLogger(__name__)


async def _finish(session: AsyncSession, run_id: str, status: WorkflowStatus, state: dict) -> None:
    run = await session.get(WorkflowRun, run_id)
    if run is None:
        return
    run.status = status
    run.state = {**(run.state or {}), **state}
    if status in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
        run.completed_at = utcnow()
    await session.flush()


async def memory_ingest(
    session: AsyncSession, run_id: str, case_id: str, *, actor: Actor = Actor.WORKFLOW
) -> dict:
    doc_id = await ingest_case_document(session, case_id)
    case = await session.get(Case, case_id)
    if case is not None:
        await record_event(
            session,
            case,
            EventType.MEMORY_STORED,
            actor=actor,
            message="Case written to semantic memory for future reference",
            result={"doc_id": doc_id},
        )
    await _finish(session, run_id, WorkflowStatus.COMPLETED, {"doc_id": doc_id})
    return {"doc_id": doc_id}


async def scheduled_refund_check(
    session: AsyncSession, run_id: str, case_id: str, *, check: int
) -> dict:
    case = await session.get(Case, case_id)
    if case is None or case.transaction_id is None:
        await _finish(session, run_id, WorkflowStatus.FAILED, {"error": "case or transaction missing"})
        return {"settlement_status": "UNKNOWN", "threshold_reached": True}

    settlement = await ledger_service.get_settlement(session, case.transaction_id)
    scheduled = await refund_service.find_scheduled_refund_for_case(session, case_id)
    deadline = scheduled.scheduled_for if scheduled else None
    threshold_reached = deadline is not None and utcnow() >= deadline

    run = await session.get(WorkflowRun, run_id)
    if run is not None:
        run.status = WorkflowStatus.WAITING
        run.attempts = check
        run.next_check_at = utcnow() + timedelta(seconds=20)
        run.state = {
            **(run.state or {}),
            "checks": check,
            "settlement_status": settlement.status.value,
        }

    await record_event(
        session,
        case,
        EventType.WORKFLOW_TICK,
        actor=Actor.WORKFLOW,
        message=f"Settlement check {check}: {settlement.status.value}",
        status=EventStatus.INFO,
        result={"settlement_status": settlement.status.value, "check": check},
    )
    await session.flush()
    return {
        "settlement_status": settlement.status.value,
        "threshold_reached": threshold_reached,
        "checks": check,
    }


async def scheduled_refund_complete(session: AsyncSession, run_id: str, case_id: str) -> dict:
    await _finish(session, run_id, WorkflowStatus.COMPLETED, {"decision": "SETTLEMENT_COMPLETED"})
    return {"decision": "SETTLEMENT_COMPLETED"}


async def scheduled_refund_execute(session: AsyncSession, run_id: str, case_id: str) -> dict:
    """The deadline passed. Hand back to the supervisor, which owns the decision."""
    await _finish(session, run_id, WorkflowStatus.COMPLETED, {"decision": "REFUND_CONDITION_MET"})
    return {"decision": "REFUND_CONDITION_MET"}


async def settlement_scan(
    session: AsyncSession, run_id: str, settings: Settings, *, runner=None
) -> list[dict]:
    """Proactive monitoring: find overdue settlements nobody has complained about."""
    delayed = await ledger_service.find_delayed_settlements(
        session, settings.settlement_delay_grace_seconds
    )
    actionable = [row for row in delayed if not row["has_open_case"]]

    created: list[dict] = []
    for row in actionable:
        case = Case(
            id=await next_id(session, "case"),
            merchant_id=row["merchant_id"],
            transaction_id=row["transaction_id"],
            status=CaseStatus.RECEIVED,
            origin=CaseOrigin.PROACTIVE,
            original_message=(
                f"Proactive check: settlement for {row['transaction_id']} was expected "
                f"{_overdue_phrase(row['overdue_seconds'])} ago and is still pending "
                f"({row['delay_reason']})."
            ),
            scenario="proactive",
        )
        session.add(case)
        await session.flush()

        await record_event(
            session,
            case,
            EventType.CASE_CREATED,
            actor=Actor.SYSTEM,
            message="Proactive case opened before the merchant reported anything",
            result={"transaction_id": row["transaction_id"], "origin": "PROACTIVE"},
        )
        alert = ProactiveAlert(
            id=await next_id(session, "alert"),
            case_id=case.id,
            merchant_id=row["merchant_id"],
            transaction_id=row["transaction_id"],
            overdue_seconds=row["overdue_seconds"],
        )
        session.add(alert)
        await session.flush()

        await record_event(
            session,
            case,
            EventType.PROACTIVE_ALERT_CREATED,
            actor=Actor.SYSTEM,
            message=(
                f"Settlement delay detected on {row['transaction_id']}, expected "
                f"{_overdue_phrase(row['overdue_seconds'])} ago"
            ),
            status=EventStatus.WARNING,
            result={
                "alert_id": alert.id,
                "transaction_id": row["transaction_id"],
                "overdue_seconds": row["overdue_seconds"],
            },
        )
        created.append({"case_id": case.id, "transaction_id": row["transaction_id"]})

    await _finish(
        session,
        run_id,
        WorkflowStatus.COMPLETED,
        {"scanned": len(delayed), "created": len(created)},
    )
    await session.commit()

    if runner is not None:
        for entry in created:
            await runner.start(entry["case_id"], trigger="PROACTIVE")
    return created


def _overdue_phrase(seconds: int) -> str:
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"
