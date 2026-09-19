"""Case endpoints.

Creating a case returns immediately and runs the supervisor in the background,
so the dashboard can poll the timeline and watch the agent work rather than
waiting on a long request.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.context import build_context
from ..agent.events import EventType, list_events, record_event
from ..database.enums import Actor, CaseOrigin, CaseStatus, MessageChannel
from ..database.ids import next_id
from ..database.models import Case
from ..runtime import SaarthiRuntime
from ..services import ops_service
from ..workflows.engine import list_runs
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/cases", tags=["cases"])


class CreateCaseRequest(BaseModel):
    merchant_id: str = "M1001"
    message: str
    transaction_id: str | None = None
    channel: MessageChannel = MessageChannel.CHAT
    scenario: str | None = None


class MessageRequest(BaseModel):
    message: str
    channel: MessageChannel = MessageChannel.CHAT


class ResumeRequest(BaseModel):
    trigger: str = "SETTLEMENT_UPDATE"


class CaseResponse(BaseModel):
    id: str
    status: CaseStatus
    origin: CaseOrigin
    merchant_id: str
    transaction_id: str | None
    intent: str | None
    diagnosis: dict[str, Any] | None
    risk: str | None
    policy: dict[str, Any] | None
    current_action: dict[str, Any] | None
    requires_human: bool
    human_required_by_policy: bool
    owner: str
    wait_reason: str | None
    resolution: str | None
    pending_escalation_id: str | None = None
    original_message: str
    created_at: str
    updated_at: str
    resolved_at: str | None


def _serialise(case: Case, pending_escalation_id: str | None = None) -> CaseResponse:
    policy = None
    if case.policy_decisions:
        policy = case.policy_decisions[-1]
    return CaseResponse(
        id=case.id,
        status=case.status,
        origin=case.origin,
        merchant_id=case.merchant_id,
        transaction_id=case.transaction_id,
        intent=case.intent,
        diagnosis=case.diagnosis,
        risk=case.risk.value if case.risk else None,
        policy=policy,
        current_action=case.current_action,
        requires_human=case.requires_human,
        human_required_by_policy=case.human_required_by_policy,
        owner=case.owner.value,
        wait_reason=case.wait_reason,
        resolution=case.resolution.value if case.resolution else None,
        pending_escalation_id=pending_escalation_id,
        original_message=case.original_message,
        created_at=case.created_at.isoformat(),
        updated_at=case.updated_at.isoformat(),
        resolved_at=case.resolved_at.isoformat() if case.resolved_at else None,
    )


async def _with_escalation(
    session: AsyncSession, runtime: SaarthiRuntime, case: Case
) -> CaseResponse:
    pending = await runtime.escalation.pending_for_case(session, case.id)
    return _serialise(case, pending.id if pending else None)


@router.post("", status_code=202, response_model=CaseResponse)
async def create_case(
    payload: CreateCaseRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> CaseResponse:
    case = Case(
        id=await next_id(session, "case"),
        merchant_id=payload.merchant_id,
        transaction_id=payload.transaction_id,
        status=CaseStatus.RECEIVED,
        origin=(
            CaseOrigin.MERCHANT_VOICE
            if payload.channel == MessageChannel.VOICE
            else CaseOrigin.MERCHANT_CHAT
        ),
        original_message=payload.message,
        scenario=payload.scenario,
    )
    session.add(case)
    await session.flush()

    await record_event(
        session, case, EventType.CASE_CREATED, actor=Actor.MERCHANT, message="New merchant case received"
    )
    await ops_service.record_inbound_message(
        session, case_id=case.id, content=payload.message, channel=payload.channel
    )
    await record_event(
        session,
        case,
        EventType.MESSAGE_RECEIVED,
        actor=Actor.MERCHANT,
        message=payload.message,
        meta={"channel": payload.channel.value},
    )
    await session.commit()

    await runtime.runner.start(case.id)
    await session.refresh(case)
    return await _with_escalation(session, runtime, case)


@router.get("", response_model=list[CaseResponse])
async def list_cases(
    status: CaseStatus | None = None,
    limit: int = Query(50, le=200),
    session: AsyncSession = Depends(get_session),
) -> list[CaseResponse]:
    stmt = select(Case).order_by(Case.created_at.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Case.status == status)
    rows = await session.scalars(stmt)
    return [_serialise(c) for c in rows]


@router.get("/{case_id}", response_model=CaseResponse)
async def get_case(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> CaseResponse:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    return await _with_escalation(session, runtime, case)


@router.post("/{case_id}/message", status_code=202, response_model=CaseResponse)
async def post_message(
    case_id: str,
    payload: MessageRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> CaseResponse:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")

    # "This happened again" on a closed case opens a new one, linked back.
    if case.status == CaseStatus.RESOLVED:
        follow_up = Case(
            id=await next_id(session, "case"),
            merchant_id=case.merchant_id,
            transaction_id=None,
            status=CaseStatus.RECEIVED,
            origin=case.origin,
            original_message=payload.message,
        )
        session.add(follow_up)
        await session.flush()
        await record_event(
            session,
            follow_up,
            EventType.CASE_CREATED,
            actor=Actor.MERCHANT,
            message="New merchant case received",
            meta={"previous_case_id": case.id},
        )
        await ops_service.record_inbound_message(
            session, case_id=follow_up.id, content=payload.message, channel=payload.channel
        )
        await record_event(
            session,
            follow_up,
            EventType.MESSAGE_RECEIVED,
            actor=Actor.MERCHANT,
            message=payload.message,
            meta={"channel": payload.channel.value},
        )
        await session.commit()
        await runtime.runner.start(follow_up.id)
        await session.refresh(follow_up)
        return await _with_escalation(session, runtime, follow_up)

    await ops_service.record_inbound_message(
        session, case_id=case.id, content=payload.message, channel=payload.channel
    )
    await record_event(
        session,
        case,
        EventType.MESSAGE_RECEIVED,
        actor=Actor.MERCHANT,
        message=payload.message,
        meta={"channel": payload.channel.value},
    )
    await session.commit()

    # A parked case is driven by a human or a workflow, not by chatter.
    if case.status == CaseStatus.RECEIVED:
        await runtime.runner.start(case.id)
    await session.refresh(case)
    return await _with_escalation(session, runtime, case)


@router.post("/{case_id}/resume", status_code=202)
async def resume_case(
    case_id: str,
    payload: ResumeRequest,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")
    await runtime.runner.resume(case_id, trigger=payload.trigger)
    return {"case_id": case_id, "trigger": payload.trigger}


@router.get("/{case_id}/timeline")
async def get_timeline(case_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    events = await list_events(session, case_id)
    return {
        "case_id": case_id,
        "events": [
            {
                "id": e.id,
                "case_id": e.case_id,
                "type": e.event_type,
                "status": e.status.value,
                "actor": e.actor.value,
                "message": e.message,
                "result": e.result,
                "metadata": e.meta,
                "sequence": e.sequence,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ],
    }


@router.get("/{case_id}/context")
async def get_case_context(
    case_id: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    case = await session.get(Case, case_id)
    if case is None:
        raise HTTPException(404, f"Case {case_id} not found")

    memory = None
    try:
        memory = await runtime.memory.search(
            session,
            text=case.original_message,
            merchant_id=case.merchant_id,
            transaction_id=case.transaction_id,
            intent_hint=case.intent,
            exclude_case_id=case.id,
        )
    except Exception:  # noqa: BLE001
        memory = None

    ctx = await build_context(session, case, memory=memory)
    return {
        "case_id": case.id,
        "merchant": ctx.merchant,
        "transaction": ctx.transaction,
        "payment_history": ctx.payment_history,
        "settlement": ctx.settlement,
        "settlement_eta": ctx.settlement_eta,
        "disputes": ctx.disputes,
        "refunds": ctx.refunds,
        "merchant_history": ctx.merchant_history,
        # Evidence, already checked against the ledger. The UI marks it as such.
        "notifications": ctx.notifications,
        "patterns": ctx.patterns,
        "policy": case.policy_decisions[-1] if case.policy_decisions else None,
        "memory": memory,
    }


@router.get("/{case_id}/messages")
async def get_messages(case_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    messages = await ops_service.list_messages(session, case_id)
    return {
        "case_id": case_id,
        "messages": [
            {
                "id": m.id,
                "direction": m.direction.value,
                "channel": m.channel.value,
                "sender": m.sender.value,
                "content": m.content,
                "status": m.status.value,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
            # Unsent drafts are internal; the merchant has not seen them. The
            # speech endpoint asks the same question of the same predicate.
            if ops_service.is_merchant_visible(m)
        ],
    }


@router.get("/{case_id}/workflows")
async def get_workflows(case_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    runs = await list_runs(session, case_id)
    return {
        "case_id": case_id,
        "runs": [
            {
                "id": r.id,
                "workflow": r.workflow,
                "engine": r.engine,
                "status": r.status.value,
                "attempts": r.attempts,
                "state": r.state,
                "next_check_at": r.next_check_at.isoformat() if r.next_check_at else None,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ],
    }
