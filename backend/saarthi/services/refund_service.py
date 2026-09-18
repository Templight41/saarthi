"""Refund operations.

This module carries the demo's integrity. Two invariants matter:

  1. A simulated failure is raised BEFORE any row is written, so verification
     genuinely finds nothing and a retry is genuinely safe.
  2. Every refund is keyed by an idempotency key with a unique constraint, so
     a replay returns the existing refund instead of issuing a second one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import PaymentStatus, RefundStatus, SettlementStatus
from ..database.ids import next_id
from ..database.models import Refund, Settlement, Transaction
from ..simulation.failure_injection import SimulationState
from .errors import invalid_state, not_found, upstream_timeout

LIVE_REFUND_STATES = {
    RefundStatus.SCHEDULED,
    RefundStatus.PENDING,
    RefundStatus.PROCESSING,
    RefundStatus.COMPLETED,
}


async def find_refund_by_idempotency_key(session: AsyncSession, key: str) -> Refund | None:
    return await session.scalar(select(Refund).where(Refund.idempotency_key == key))


async def get_refund(session: AsyncSession, refund_id: str) -> Refund:
    refund = await session.get(Refund, refund_id)
    if refund is None:
        raise not_found("Refund", refund_id)
    return refund


async def list_refunds_for_transaction(session: AsyncSession, transaction_id: str) -> list[Refund]:
    result = await session.scalars(
        select(Refund).where(Refund.transaction_id == transaction_id).order_by(Refund.created_at)
    )
    return list(result)


async def _validate_refundable(session: AsyncSession, transaction_id: str, amount: Decimal) -> Transaction:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        raise not_found("Transaction", transaction_id)

    settlement = await session.scalar(select(Settlement).where(Settlement.transaction_id == transaction_id))

    settled_ok = txn.payment_status == PaymentStatus.SUCCESS
    debited_and_failed = (
        txn.payment_status == PaymentStatus.PAYMENT_PENDING
        and txn.customer_debited
        and settlement is not None
        and settlement.status == SettlementStatus.FAILED
    )
    already_refunded = txn.payment_status == PaymentStatus.REFUNDED

    if already_refunded:
        raise invalid_state(f"Transaction {transaction_id} has already been fully refunded")
    if not (settled_ok or debited_and_failed):
        raise invalid_state(
            f"Transaction {transaction_id} is in state {txn.payment_status.value} "
            "and cannot be refunded right now"
        )

    remaining = Decimal(txn.amount) - Decimal(txn.refunded_amount)
    if amount > remaining:
        raise EnterpriseAmountError(amount, remaining, transaction_id)
    return txn


class EnterpriseAmountError(Exception):
    """Raised as a typed 409 by the caller; kept separate for a clearer message."""

    def __init__(self, amount: Decimal, remaining: Decimal, transaction_id: str) -> None:
        from .errors import EnterpriseAPIError

        self.wrapped = EnterpriseAPIError(
            409,
            "AMOUNT_EXCEEDS_REFUNDABLE",
            f"Refund of {amount} exceeds the {remaining} still refundable on {transaction_id}",
            retryable=False,
        )
        super().__init__(self.wrapped.message)


async def issue_refund(
    session: AsyncSession,
    *,
    transaction_id: str,
    amount: Decimal,
    reason: str,
    idempotency_key: str,
    case_id: str | None,
    simulation: SimulationState,
) -> Refund:
    # 1. Idempotent replay: never issue a second refund for the same key.
    existing = await find_refund_by_idempotency_key(session, idempotency_key)
    if existing is not None and existing.status in LIVE_REFUND_STATES:
        if existing.status == RefundStatus.SCHEDULED:
            # Promote a scheduled refund into an executed one.
            existing.status = simulation.refund_result_status
            existing.attempt_count += 1
            if existing.status == RefundStatus.COMPLETED:
                existing.completed_at = utcnow()
                await _apply_refund_to_transaction(session, transaction_id, Decimal(existing.amount))
            await session.flush()
        return existing

    # 2. State validation happens before any side effect.
    try:
        await _validate_refundable(session, transaction_id, amount)
    except EnterpriseAmountError as exc:
        raise exc.wrapped from None

    attempts = simulation.record_attempt(idempotency_key)

    # 3. Injected failure raises BEFORE writing, so nothing was applied.
    if simulation.consume_refund_failure():
        raise upstream_timeout()

    refund_status = simulation.refund_result_status
    refund = Refund(
        id=await next_id(session, "refund"),
        transaction_id=transaction_id,
        case_id=case_id,
        amount=amount,
        status=refund_status,
        reason=reason,
        attempt_count=attempts,
        idempotency_key=idempotency_key,
        completed_at=utcnow() if refund_status == RefundStatus.COMPLETED else None,
    )
    session.add(refund)
    try:
        await session.flush()
    except IntegrityError:
        # Concurrent attempt won the race; return theirs rather than duplicating.
        await session.rollback()
        winner = await find_refund_by_idempotency_key(session, idempotency_key)
        if winner is not None:
            return winner
        raise

    if refund_status == RefundStatus.COMPLETED:
        await _apply_refund_to_transaction(session, transaction_id, amount)
    return refund


async def _apply_refund_to_transaction(
    session: AsyncSession, transaction_id: str, amount: Decimal
) -> None:
    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        return
    txn.refunded_amount = Decimal(txn.refunded_amount) + amount
    txn.payment_status = (
        PaymentStatus.REFUNDED
        if Decimal(txn.refunded_amount) >= Decimal(txn.amount)
        else PaymentStatus.PARTIALLY_REFUNDED
    )
    await session.flush()


async def schedule_refund(
    session: AsyncSession,
    *,
    transaction_id: str,
    amount: Decimal,
    reason: str,
    condition: dict,
    deadline: datetime,
    idempotency_key: str,
    case_id: str | None,
) -> Refund:
    existing = await find_refund_by_idempotency_key(session, idempotency_key)
    if existing is not None and existing.status in LIVE_REFUND_STATES:
        return existing

    txn = await session.get(Transaction, transaction_id)
    if txn is None:
        raise not_found("Transaction", transaction_id)

    refund = Refund(
        id=await next_id(session, "refund"),
        transaction_id=transaction_id,
        case_id=case_id,
        amount=amount,
        status=RefundStatus.SCHEDULED,
        reason=reason,
        attempt_count=0,
        idempotency_key=idempotency_key,
        scheduled_condition=condition,
        scheduled_for=deadline,
    )
    session.add(refund)
    await session.flush()
    return refund


async def cancel_scheduled_refund(session: AsyncSession, refund_id: str, reason: str) -> Refund:
    refund = await get_refund(session, refund_id)
    if refund.status != RefundStatus.SCHEDULED:
        raise invalid_state(
            f"Refund {refund_id} is {refund.status.value} and cannot be cancelled"
        )
    refund.status = RefundStatus.CANCELLED
    refund.reason = f"{refund.reason} | cancelled: {reason}".strip(" |")
    await session.flush()
    return refund


async def find_scheduled_refund_for_case(session: AsyncSession, case_id: str) -> Refund | None:
    return await session.scalar(
        select(Refund).where(Refund.case_id == case_id, Refund.status == RefundStatus.SCHEDULED)
    )
