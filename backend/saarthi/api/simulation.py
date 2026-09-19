"""Demo controls.

These are what a presenter actually drives the demo with: reset, run a
scenario, arm a failure, settle or fail a settlement. All deterministic, all
repeatable.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import PaymentMethod, PaymentStatus, RefundStatus, SettlementStatus
from ..database.models import Case, ProactiveAlert, Settlement, Transaction, TransactionEvent
from ..database.seed import seed_all
from ..memory.knowledge import seed_knowledge
from ..runtime import SaarthiRuntime
from ..services import ledger_service, notification_service
from ..simulation.failure_injection import simulation_state
from ..simulation.scenarios import SCENARIOS, resolve
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


class FailureRequest(BaseModel):
    attempts: int = 1


class NewTransaction(BaseModel):
    """A hand-made fixture.

    This lives under /api/simulation because that is what it is: a way to put
    a transaction into the merchant's payment systems for testing. It is not a
    way for Saarthi to create payments — the agent has no tool that writes
    here, and adding one would put the ledger under the model's control.
    """

    merchant_id: str = "M1001"
    amount: Decimal = Decimal("1000.00")
    payment_status: PaymentStatus = PaymentStatus.SUCCESS
    payment_method: PaymentMethod = PaymentMethod.QR
    customer_debited: bool = True
    description: str = "Manually created for testing"
    customer_reference: str = ""
    #: Omit to get the next TXN id from the counters table.
    transaction_id: str | None = None
    #: None creates no settlement row at all, which some flows expect.
    settlement_status: SettlementStatus | None = SettlementStatus.PENDING
    settlement_due_in_minutes: int = 120
    #: Also record a Soundbox announcement naming this transaction.
    announce_on_soundbox: bool = False


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
    """The older shape, kept so the demo console does not break.

    `/api/scenarios` is the real catalogue; this is a projection of it.
    """
    return {
        "scenarios": [
            {
                "key": scenario.id,
                "title": scenario.name,
                "merchant_id": scenario.merchant_id,
                "transaction_id": scenario.transaction_id,
                "message": scenario.message,
                "expectation": scenario.expected_outcome,
            }
            for scenario in SCENARIOS.values()
        ]
    }


@router.post("/scenario/{name}")
async def run_scenario(
    name: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    scenario = resolve(name)
    if scenario is None:
        raise HTTPException(404, f"Unknown scenario {name}")

    await runtime.runner.shutdown()
    if runtime.workflows is not None:
        await runtime.workflows.shutdown()
    simulation_state.reset()

    await seed_all(session)
    await seed_knowledge(session, runtime.memory)
    await scenario.arm(simulation_state, session)
    simulation_state.active_scenario = scenario.id
    await session.commit()

    return {
        "scenario": scenario.id,
        "title": scenario.name,
        "merchant_id": scenario.merchant_id,
        "transaction_id": scenario.transaction_id,
        "suggested_message": scenario.message,
        "expectation": scenario.expected_outcome,
        "simulation": simulation_state.as_dict(),
    }


@router.get("/transactions")
async def list_transactions(
    merchant_id: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Every transaction, with its settlement. There is no other way to see them."""
    stmt = select(Transaction).order_by(Transaction.created_at.desc()).limit(limit)
    if merchant_id:
        stmt = stmt.where(Transaction.merchant_id == merchant_id)
    rows = list(await session.scalars(stmt))

    settlements = {}
    if rows:
        found = await session.scalars(
            select(Settlement).where(Settlement.transaction_id.in_([t.id for t in rows]))
        )
        settlements = {s.transaction_id: s for s in found}

    return {
        "transactions": [
            {
                "id": t.id,
                "merchant_id": t.merchant_id,
                "amount": str(t.amount),
                "currency": t.currency,
                "payment_status": t.payment_status.value,
                "payment_method": t.payment_method.value,
                "customer_debited": t.customer_debited,
                "customer_reference": t.customer_reference,
                "description": t.description,
                "refunded_amount": str(t.refunded_amount),
                "created_at": t.created_at.isoformat(),
                "settlement": (
                    {
                        "id": settlements[t.id].id,
                        "status": settlements[t.id].status.value,
                        "expected_at": (
                            settlements[t.id].expected_at.isoformat()
                            if settlements[t.id].expected_at
                            else None
                        ),
                    }
                    if t.id in settlements
                    else None
                ),
            }
            for t in rows
        ]
    }


@router.post("/transactions", status_code=201)
async def create_transaction(
    payload: NewTransaction,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Put a transaction into the ledger by hand, for testing.

    Note that `/api/simulation/reset` and running any scenario wipe the
    fixtures, so anything created here goes with them. That is deliberate:
    the demo is repeatable because the world is rebuilt from seed, and a
    surviving hand-made row would quietly break that.
    """
    from datetime import timedelta

    from ..database.database import utcnow
    from ..database.ids import next_id
    from ..database.models import Merchant
    from ..database.models import Transaction as TransactionModel

    merchant = await session.get(Merchant, payload.merchant_id)
    if merchant is None:
        raise HTTPException(404, f"Merchant {payload.merchant_id} not found")

    txn_id = payload.transaction_id or await next_id(session, "transaction")
    if await session.get(TransactionModel, txn_id):
        raise HTTPException(409, f"{txn_id} already exists")

    now = utcnow()
    txn = TransactionModel(
        id=txn_id,
        merchant_id=payload.merchant_id,
        amount=payload.amount,
        payment_status=payload.payment_status,
        payment_method=payload.payment_method,
        customer_debited=payload.customer_debited,
        customer_reference=payload.customer_reference or f"CUST-{txn_id[-4:]}",
        description=payload.description,
        created_at=now,
    )
    session.add(txn)
    await session.flush()

    settlement = None
    if payload.settlement_status is not None:
        completed = payload.settlement_status == SettlementStatus.COMPLETED
        settlement = Settlement(
            id=await _free_settlement_id(session),
            transaction_id=txn_id,
            status=payload.settlement_status,
            expected_at=now + timedelta(minutes=payload.settlement_due_in_minutes),
            completed_at=now if completed else None,
        )
        session.add(settlement)

    announcement = None
    if payload.announce_on_soundbox:
        announcement = await notification_service.record_announcement(
            session,
            merchant_id=payload.merchant_id,
            reference=txn_id,
            announced_amount=payload.amount,
            device_id="SB-MANUAL",
        )

    await session.commit()
    return {
        "transaction_id": txn_id,
        "settlement_id": settlement.id if settlement else None,
        "notification_id": announcement.id if announcement else None,
    }


async def _free_settlement_id(session: AsyncSession) -> str:
    """The seed hardcodes STL-20xx ids without advancing the counter, so the
    next id from the counter can already be taken. Skip past those rather than
    renumbering fixtures the demo script quotes."""
    from ..database.ids import next_id

    for _ in range(200):
        candidate = await next_id(session, "settlement")
        if await session.get(Settlement, candidate) is None:
            return candidate
    raise HTTPException(500, "Could not allocate a settlement id")


@router.delete("/transactions/{transaction_id}", status_code=204)
async def delete_transaction(
    transaction_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    """Only for something created by hand. Anything with a case attached stays."""
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        raise HTTPException(404, f"{transaction_id} not found")

    attached = await session.scalar(
        select(Case.id).where(Case.transaction_id == transaction_id).limit(1)
    )
    if attached:
        raise HTTPException(
            409, f"{transaction_id} belongs to case {attached}; reset the fixtures instead"
        )

    await session.execute(
        delete(Settlement).where(Settlement.transaction_id == transaction_id)
    )
    await session.execute(
        delete(TransactionEvent).where(TransactionEvent.transaction_id == transaction_id)
    )
    await session.delete(txn)
    await session.commit()


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
