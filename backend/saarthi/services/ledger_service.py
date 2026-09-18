"""Transaction, settlement, merchant and dispute reads/writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import (
    CaseStatus,
    DisputeStatus,
    DisputeType,
    PaymentStatus,
    SettlementStatus,
)
from ..database.ids import next_id
from ..database.models import Case, Dispute, Merchant, Settlement, Transaction, TransactionEvent
from .errors import invalid_state, not_found

PAYMENT_EVENT_KINDS = {
    "PAYMENT_INITIATED",
    "CUSTOMER_DEBITED",
    "BANK_CONFIRMATION_PENDING",
    "SETTLED",
    "SETTLEMENT_FAILED",
    "REFUNDED",
}


# --------------------------------------------------------------------------
# Transactions
# --------------------------------------------------------------------------
async def get_transaction(session: AsyncSession, transaction_id: str) -> Transaction:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        raise not_found("Transaction", transaction_id)
    return txn


async def get_transaction_history(session: AsyncSession, transaction_id: str) -> list[TransactionEvent]:
    await get_transaction(session, transaction_id)
    result = await session.scalars(
        select(TransactionEvent)
        .where(TransactionEvent.transaction_id == transaction_id)
        .order_by(TransactionEvent.occurred_at, TransactionEvent.id)
    )
    return list(result)


async def get_payment_history(session: AsyncSession, transaction_id: str) -> list[TransactionEvent]:
    events = await get_transaction_history(session, transaction_id)
    return [e for e in events if e.kind in PAYMENT_EVENT_KINDS]


async def find_transactions_for_merchant(session: AsyncSession, merchant_id: str) -> list[Transaction]:
    result = await session.scalars(
        select(Transaction)
        .where(Transaction.merchant_id == merchant_id)
        .order_by(Transaction.created_at.desc())
    )
    return list(result)


# --------------------------------------------------------------------------
# Settlements
# --------------------------------------------------------------------------
@dataclass
class SettlementEta:
    transaction_id: str
    status: SettlementStatus
    expected_at: datetime | None
    eta_seconds: int | None
    overdue: bool
    delay_reason: str | None


async def get_settlement(session: AsyncSession, transaction_id: str) -> Settlement:
    stl = await session.scalar(select(Settlement).where(Settlement.transaction_id == transaction_id))
    if stl is None:
        raise not_found("Settlement for transaction", transaction_id)
    return stl


async def get_settlement_eta(session: AsyncSession, transaction_id: str) -> SettlementEta:
    stl = await get_settlement(session, transaction_id)
    eta_seconds: int | None = None
    overdue = False
    if stl.expected_at is not None and stl.status == SettlementStatus.PENDING:
        delta = (stl.expected_at - utcnow()).total_seconds()
        eta_seconds = int(delta)
        overdue = delta < 0
    return SettlementEta(
        transaction_id=transaction_id,
        status=stl.status,
        expected_at=stl.expected_at,
        eta_seconds=eta_seconds,
        overdue=overdue,
        delay_reason=stl.delay_reason,
    )


async def complete_settlement(session: AsyncSession, transaction_id: str) -> Settlement:
    """Simulation control: settle the transaction and mark the payment success."""
    stl = await get_settlement(session, transaction_id)
    if stl.status == SettlementStatus.COMPLETED:
        return stl
    now = utcnow()
    stl.status = SettlementStatus.COMPLETED
    stl.completed_at = now
    stl.delay_reason = None

    txn = await get_transaction(session, transaction_id)
    if txn.payment_status == PaymentStatus.PAYMENT_PENDING:
        txn.payment_status = PaymentStatus.SUCCESS
    session.add(
        TransactionEvent(
            transaction_id=transaction_id,
            kind="SETTLED",
            detail={"amount": str(txn.amount)},
            occurred_at=now,
        )
    )
    await session.flush()
    return stl


async def fail_settlement(session: AsyncSession, transaction_id: str, reason: str) -> Settlement:
    """Simulation control: the settlement definitively failed."""
    stl = await get_settlement(session, transaction_id)
    stl.status = SettlementStatus.FAILED
    stl.delay_reason = reason
    session.add(
        TransactionEvent(
            transaction_id=transaction_id,
            kind="SETTLEMENT_FAILED",
            detail={"reason": reason},
            occurred_at=utcnow(),
        )
    )
    await session.flush()
    return stl


async def find_delayed_settlements(session: AsyncSession, grace_seconds: int) -> list[dict]:
    """Pending settlements past their expected time, excluding those with an open case."""
    cutoff = utcnow()
    rows = await session.execute(
        select(Settlement, Transaction)
        .join(Transaction, Transaction.id == Settlement.transaction_id)
        .where(
            Settlement.status == SettlementStatus.PENDING,
            Settlement.monitor_armed.is_(True),
            Settlement.expected_at.is_not(None),
        )
    )
    out: list[dict] = []
    for stl, txn in rows:
        if stl.expected_at is None:
            continue
        overdue = (cutoff - stl.expected_at).total_seconds()
        if overdue < grace_seconds:
            continue
        open_case = await session.scalar(
            select(Case.id).where(
                Case.transaction_id == txn.id,
                Case.status.not_in([CaseStatus.RESOLVED, CaseStatus.ESCALATED]),
            )
        )
        out.append(
            {
                "transaction_id": txn.id,
                "merchant_id": txn.merchant_id,
                "amount": str(txn.amount),
                "expected_at": stl.expected_at.isoformat(),
                "overdue_seconds": int(overdue),
                "delay_reason": stl.delay_reason,
                "has_open_case": open_case is not None,
            }
        )
    return out


# --------------------------------------------------------------------------
# Disputes
# --------------------------------------------------------------------------
async def get_disputes(session: AsyncSession, transaction_id: str) -> list[Dispute]:
    result = await session.scalars(
        select(Dispute).where(Dispute.transaction_id == transaction_id).order_by(Dispute.created_at)
    )
    return list(result)


async def create_dispute(
    session: AsyncSession,
    *,
    transaction_id: str,
    type_: DisputeType,
    description: str,
    requested_amount: Decimal | None,
    evidence: dict | None = None,
) -> Dispute:
    await get_transaction(session, transaction_id)
    dispute = Dispute(
        id=await next_id(session, "dispute"),
        transaction_id=transaction_id,
        type=type_,
        description=description,
        requested_amount=requested_amount,
        status=DisputeStatus.OPEN,
        evidence=evidence or {},
    )
    session.add(dispute)
    await session.flush()
    return dispute


async def update_dispute(session: AsyncSession, dispute_id: str, *, status: DisputeStatus) -> Dispute:
    dispute = await session.get(Dispute, dispute_id)
    if dispute is None:
        raise not_found("Dispute", dispute_id)
    if dispute.status in {DisputeStatus.RESOLVED, DisputeStatus.REJECTED}:
        raise invalid_state(f"Dispute {dispute_id} is already {dispute.status.value}")
    dispute.status = status
    await session.flush()
    return dispute


# --------------------------------------------------------------------------
# Merchants
# --------------------------------------------------------------------------
async def get_merchant(session: AsyncSession, merchant_id: str) -> Merchant:
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None:
        raise not_found("Merchant", merchant_id)
    return merchant


async def get_merchant_history(session: AsyncSession, merchant_id: str, limit: int = 10) -> list[Case]:
    """Resolved cases only. This is authoritative history, from Postgres."""
    result = await session.scalars(
        select(Case)
        .where(Case.merchant_id == merchant_id, Case.status == CaseStatus.RESOLVED)
        .order_by(Case.resolved_at.desc())
        .limit(limit)
    )
    return list(result)
