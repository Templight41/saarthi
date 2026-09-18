"""Demo controls.

These are what a presenter actually drives the demo with: reset, run a
scenario, arm a failure, settle or fail a settlement. All deterministic, all
repeatable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import RefundStatus, SettlementStatus
from ..database.models import Case, ProactiveAlert, Settlement
from ..database.seed import seed_all
from ..memory.knowledge import seed_knowledge
from ..runtime import SaarthiRuntime
from ..services import ledger_service
from ..simulation.failure_injection import simulation_state
from ..simulation.scenarios import SCENARIOS
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


class FailureRequest(BaseModel):
    attempts: int = 1


@router.post("/reset")
async def reset(
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    await runtime.runner.shutdown()
    if runtime.workflows is not None:
        await runtime.workflows.shutdown()
    simulation_state.reset()

    info = await seed_all(session)
    count = await seed_knowledge(session, runtime.memory)
    await session.commit()
    return {**info, "knowledge_documents": count, "simulation": simulation_state.as_dict()}


@router.get("/scenarios")
async def list_scenarios() -> dict:
    return {
        "scenarios": [
            {
                "key": key,
                "title": scenario.title,
                "merchant_id": scenario.merchant_id,
                "transaction_id": scenario.transaction_id,
                "message": scenario.message,
                "expectation": scenario.expectation,
            }
            for key, scenario in SCENARIOS.items()
        ]
    }


@router.post("/scenario/{name}")
async def run_scenario(
    name: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    key = name.upper() if name.upper() in SCENARIOS else name.lower()
    scenario = SCENARIOS.get(key)
    if scenario is None:
        raise HTTPException(404, f"Unknown scenario {name}")

    await runtime.runner.shutdown()
    if runtime.workflows is not None:
        await runtime.workflows.shutdown()
    simulation_state.reset()

    await seed_all(session)
    await seed_knowledge(session, runtime.memory)
    scenario.arm(simulation_state, session)
    simulation_state.active_scenario = key
    await session.commit()

    return {
        "scenario": key,
        "title": scenario.title,
        "merchant_id": scenario.merchant_id,
        "transaction_id": scenario.transaction_id,
        "suggested_message": scenario.message,
        "expectation": scenario.expectation,
        "simulation": simulation_state.as_dict(),
    }


@router.post("/failure/refund")
async def arm_refund_failure(payload: FailureRequest) -> dict:
    simulation_state.fail_next_refund_attempts = payload.attempts
    return simulation_state.as_dict()


@router.post("/settlement/{transaction_id}/complete")
async def complete_settlement(
    transaction_id: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    await ledger_service.complete_settlement(session, transaction_id)
    await session.commit()
    resumed = await _resume_cases_for(session, runtime, transaction_id, "SETTLEMENT_UPDATE")
    return {"transaction_id": transaction_id, "status": "COMPLETED", "resumed_cases": resumed}


@router.post("/settlement/{transaction_id}/fail")
async def fail_settlement(
    transaction_id: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    await ledger_service.fail_settlement(session, transaction_id, "BANK_REJECTED")
    await session.commit()
    resumed = await _resume_cases_for(session, runtime, transaction_id, "SETTLEMENT_UPDATE")
    return {"transaction_id": transaction_id, "status": "FAILED", "resumed_cases": resumed}


@router.post("/proactive")
async def run_proactive(
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    """Arm the overdue settlement and run the monitor immediately."""
    from datetime import timedelta

    from ..database.database import utcnow

    settlement = await session.scalar(
        select(Settlement).where(Settlement.transaction_id == "TXN19931")
    )
    if settlement is None:
        raise HTTPException(404, "Proactive demo transaction is not seeded")
    settlement.monitor_armed = True
    settlement.expected_at = utcnow() - timedelta(hours=2)
    settlement.status = SettlementStatus.PENDING
    await session.commit()

    run = await runtime.workflows.run_now(session, "settlement_monitor")
    await session.commit()
    return {"workflow_run_id": run.id, "transaction_id": "TXN19931"}


@router.get("/state")
async def get_state() -> dict:
    return simulation_state.as_dict()


@router.get("/alerts")
async def list_alerts(session: AsyncSession = Depends(get_session)) -> dict:
    rows = await session.scalars(
        select(ProactiveAlert).where(ProactiveAlert.status == "ACTIVE").order_by(
            ProactiveAlert.created_at.desc()
        )
    )
    alerts = []
    for alert in rows:
        merchant = await ledger_service.get_merchant(session, alert.merchant_id)
        alerts.append(
            {
                "id": alert.id,
                "case_id": alert.case_id,
                "merchant_id": alert.merchant_id,
                "merchant_name": merchant.name,
                "transaction_id": alert.transaction_id,
                "overdue_seconds": alert.overdue_seconds,
                "created_at": alert.created_at.isoformat(),
            }
        )
    return {"alerts": alerts}


async def _resume_cases_for(
    session: AsyncSession, runtime: SaarthiRuntime, transaction_id: str, trigger: str
) -> list[str]:
    from ..database.enums import CaseStatus

    rows = await session.scalars(
        select(Case).where(
            Case.transaction_id == transaction_id,
            Case.status.not_in([CaseStatus.RESOLVED]),
        )
    )
    resumed = []
    for case in rows:
        await runtime.runner.resume(case.id, trigger=trigger)
        resumed.append(case.id)
    return resumed


def _unused(status: RefundStatus) -> None:  # pragma: no cover
    return None
