"""Human approval workspace.

Approve, reject and take over. Approve does not execute anything directly: it
records the override, then hands back to the supervisor, which re-checks policy
with the override attached and runs the action through the same executor and
verifier as any autonomous action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import EventType, record_event
from ..database.database import utcnow
from ..database.enums import Actor, CaseOwner, EscalationStatus, EventStatus, Resolution
from ..database.models import Case, Escalation
from ..runtime import SaarthiRuntime
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/escalations", tags=["escalations"])


class DecisionRequest(BaseModel):
    decided_by: str = "ops@urbanthreads.in"
    note: str | None = None


class TakeoverRequest(BaseModel):
    assigned_to: str = "ops@urbanthreads.in"
    note: str | None = None


def _serialise(escalation: Escalation, case: Case | None = None) -> dict:
    return {
        "id": escalation.id,
        "case_id": escalation.case_id,
        "reason": escalation.reason,
        "risk": escalation.risk.value if escalation.risk else None,
        "amount": str(escalation.amount) if escalation.amount is not None else None,
        "recommendation": escalation.recommendation,
        "status": escalation.status.value,
        "assigned_to": escalation.assigned_to,
        "human_decision": escalation.human_decision,
        "pending_action": escalation.pending_action,
        "completed_actions": escalation.completed_actions,
        "context_snapshot": escalation.context_snapshot,
        "policy": escalation.policy,
        "decided_by": escalation.decided_by,
        "created_at": escalation.created_at.isoformat(),
        "resolved_at": escalation.resolved_at.isoformat() if escalation.resolved_at else None,
        "case_summary": (case.diagnosis or {}).get("summary", "") if case else "",
        "merchant_id": case.merchant_id if case else None,
        "transaction_id": case.transaction_id if case else None,
    }


@router.get("")
async def list_escalations(
    status: EscalationStatus | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    stmt = select(Escalation).order_by(Escalation.created_at.desc())
    if status is not None:
        stmt = stmt.where(Escalation.status == status)
    rows = list(await session.scalars(stmt))
    out = []
    for escalation in rows:
        case = await session.get(Case, escalation.case_id)
        out.append(_serialise(escalation, case))
    return {"escalations": out}


@router.get("/{escalation_id}")
async def get_escalation(escalation_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(404, f"Escalation {escalation_id} not found")
    case = await session.get(Case, escalation.case_id)
    return _serialise(escalation, case)


async def _load_pending(session: AsyncSession, escalation_id: str) -> tuple[Escalation, Case]:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(404, f"Escalation {escalation_id} not found")
    if escalation.status != EscalationStatus.PENDING_HUMAN:
        raise HTTPException(409, f"Escalation {escalation_id} is already {escalation.status.value}")
    case = await session.get(Case, escalation.case_id)
    if case is None:
        raise HTTPException(404, f"Case {escalation.case_id} not found")
    return escalation, case


@router.post("/{escalation_id}/approve", status_code=202)
async def approve(
    escalation_id: str,
    payload: DecisionRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    escalation, case = await _load_pending(session, escalation_id)
    now = utcnow()

    escalation.status = EscalationStatus.APPROVED
    escalation.human_decision = {
        "decision": "APPROVE",
        "note": payload.note,
        "decided_at": now.isoformat(),
    }
    escalation.decided_by = payload.decided_by
    escalation.decided_at = now
    escalation.resolved_at = now

    # The override is what the policy engine will consume on resume.
    case.human_override = {
        "escalation_id": escalation.id,
        "decided_by": payload.decided_by,
        "decided_at": now.isoformat(),
        "note": payload.note,
    }

    await record_event(
        session,
        case,
        EventType.HUMAN_APPROVED,
        actor=Actor.HUMAN,
        message=f"Approved by {payload.decided_by}",
        result={"escalation_id": escalation.id, "note": payload.note},
    )
    await session.commit()

    await runtime.runner.resume(case.id, trigger="HUMAN_APPROVED")
    return {"escalation_id": escalation.id, "case_id": case.id, "status": "APPROVED"}


@router.post("/{escalation_id}/reject", status_code=202)
async def reject(
    escalation_id: str,
    payload: DecisionRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    escalation, case = await _load_pending(session, escalation_id)
    now = utcnow()

    escalation.status = EscalationStatus.REJECTED
    escalation.human_decision = {
        "decision": "REJECT",
        "note": payload.note,
        "decided_at": now.isoformat(),
    }
    escalation.decided_by = payload.decided_by
    escalation.decided_at = now
    escalation.resolved_at = now

    await record_event(
        session,
        case,
        EventType.HUMAN_REJECTED,
        actor=Actor.HUMAN,
        message=f"Rejected by {payload.decided_by}",
        status=EventStatus.WARNING,
        result={"escalation_id": escalation.id, "note": payload.note},
    )
    await session.commit()

    await runtime.runner.resume(case.id, trigger="HUMAN_REJECTED")
    return {"escalation_id": escalation.id, "case_id": case.id, "status": "REJECTED"}


@router.post("/{escalation_id}/takeover", status_code=202)
async def takeover(
    escalation_id: str,
    payload: TakeoverRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    escalation, case = await _load_pending(session, escalation_id)
    now = utcnow()

    escalation.status = EscalationStatus.TAKEN_OVER
    escalation.assigned_to = payload.assigned_to
    escalation.human_decision = {
        "decision": "TAKE_OVER",
        "note": payload.note,
        "decided_at": now.isoformat(),
    }
    escalation.decided_by = payload.assigned_to
    escalation.decided_at = now

    # Ownership transfers; the agent stands down on every subsequent entry.
    case.owner = CaseOwner.HUMAN
    case.resolution = Resolution.HUMAN_TAKEOVER

    await record_event(
        session,
        case,
        EventType.HUMAN_TAKEOVER,
        actor=Actor.HUMAN,
        message=f"Autonomous execution stopped; case owned by {payload.assigned_to}",
        status=EventStatus.WARNING,
        result={"escalation_id": escalation.id, "assigned_to": payload.assigned_to},
    )
    await session.commit()

    await runtime.runner.cancel(case.id)
    return {"escalation_id": escalation.id, "case_id": case.id, "status": "TAKEN_OVER"}
