"""Endpoints n8n calls back into.

Every one of these is a thin wrapper over the same step function the local
engine calls directly, so switching engines cannot change behaviour.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database.enums import EscalationStatus
from ..database.models import Case, Escalation
from ..memory.service import ingest_case_document
from ..runtime import SaarthiRuntime
from ..services import ledger_service
from ..workflows import steps
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/internal", tags=["internal"])


def require_token(x_saarthi_internal_token: str | None = Header(None)) -> None:
    expected = get_settings().internal_api_token
    if expected and x_saarthi_internal_token != expected:
        raise HTTPException(401, "Invalid internal token")


class RunPayload(BaseModel):
    run_id: str | None = None
    case_id: str | None = None


class WorkflowRegistration(BaseModel):
    run_id: str | None = None
    resume_url: str | None = None
    execution_id: str | None = None


@router.get("/cases/{case_id}/memory-document", dependencies=[Depends(require_token)])
async def memory_document(case_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    doc_id = await ingest_case_document(session, case_id)
    await session.commit()
    return {"case_id": case_id, "doc_id": doc_id}


@router.post("/memory/ingest", dependencies=[Depends(require_token)])
async def memory_ingest(payload: RunPayload, session: AsyncSession = Depends(get_session)) -> dict:
    if not payload.case_id:
        raise HTTPException(400, "case_id is required")
    result = await steps.memory_ingest(session, payload.run_id or "", payload.case_id)
    await session.commit()
    return result


@router.post("/workflows/{run_id}/settlement", dependencies=[Depends(require_token)])
async def settlement_check(
    run_id: str, payload: RunPayload, session: AsyncSession = Depends(get_session)
) -> dict:
    if not payload.case_id:
        raise HTTPException(400, "case_id is required")
    # n8n loops this node, so the count comes from the run rather than the call.
    from ..database.models import WorkflowRun

    run = await session.get(WorkflowRun, run_id)
    check = (run.attempts if run else 0) + 1
    result = await steps.scheduled_refund_check(session, run_id, payload.case_id, check=check)
    await session.commit()
    return result


@router.post("/workflows/{run_id}/complete", dependencies=[Depends(require_token)])
async def scheduled_refund_complete(
    run_id: str,
    payload: RunPayload,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    if not payload.case_id:
        raise HTTPException(400, "case_id is required")
    result = await steps.scheduled_refund_complete(session, run_id, payload.case_id)
    await session.commit()
    await runtime.runner.resume(payload.case_id, trigger="SETTLEMENT_UPDATE")
    return result


@router.post("/workflows/{run_id}/execute", dependencies=[Depends(require_token)])
async def scheduled_refund_execute(
    run_id: str,
    payload: RunPayload,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    if not payload.case_id:
        raise HTTPException(400, "case_id is required")
    result = await steps.scheduled_refund_execute(session, run_id, payload.case_id)
    await session.commit()
    await runtime.runner.resume(payload.case_id, trigger="SCHEDULED_REFUND_DUE")
    return result


@router.get("/settlements/pending-delayed", dependencies=[Depends(require_token)])
async def pending_delayed(
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> list[dict]:
    return await ledger_service.find_delayed_settlements(
        session, runtime.settings.settlement_delay_grace_seconds
    )


@router.post("/settlements/{transaction_id}/evaluate", dependencies=[Depends(require_token)])
async def evaluate_settlement(
    transaction_id: str,
    payload: RunPayload,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    created = await steps.settlement_scan(
        session, payload.run_id or "", runtime.settings, runner=runtime.runner
    )
    match = next((c for c in created if c["transaction_id"] == transaction_id), None)
    return {"transaction_id": transaction_id, "case_id": match["case_id"] if match else None}


@router.post("/actions/{action_id}/recover", dependencies=[Depends(require_token)])
async def recover_action(action_id: str, payload: RunPayload) -> dict:
    # Recovery is driven in-process by the supervisor as part of the case loop,
    # so this reports rather than re-runs it. n8n uses the answer to stop looping.
    return {"action_id": action_id, "decision": "HANDLED_IN_PROCESS", "run_id": payload.run_id}


@router.post("/escalations/{escalation_id}/workflow", dependencies=[Depends(require_token)])
async def register_workflow(
    escalation_id: str,
    payload: WorkflowRegistration,
    session: AsyncSession = Depends(get_session),
) -> dict:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(404, f"Escalation {escalation_id} not found")
    escalation.workflow_run_id = payload.run_id
    escalation.resume_url = payload.resume_url
    await session.commit()
    return {"escalation_id": escalation_id, "resume_url": payload.resume_url}


@router.post("/escalations/{escalation_id}/notify", dependencies=[Depends(require_token)])
async def notify_escalation(
    escalation_id: str, payload: RunPayload, session: AsyncSession = Depends(get_session)
) -> dict:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(404, f"Escalation {escalation_id} not found")
    case = await session.get(Case, escalation.case_id)
    return {
        "escalation_id": escalation_id,
        "case_id": escalation.case_id,
        "reason": escalation.reason,
        "amount": str(escalation.amount) if escalation.amount else None,
        "recommendation": escalation.recommendation,
        "merchant_id": case.merchant_id if case else None,
    }


@router.post("/escalations/{escalation_id}/remind", dependencies=[Depends(require_token)])
async def remind_escalation(
    escalation_id: str, payload: RunPayload, session: AsyncSession = Depends(get_session)
) -> dict:
    escalation = await session.get(Escalation, escalation_id)
    if escalation is None:
        raise HTTPException(404, f"Escalation {escalation_id} not found")
    return {
        "escalation_id": escalation_id,
        "still_pending": escalation.status == EscalationStatus.PENDING_HUMAN,
        "status": escalation.status.value,
    }
