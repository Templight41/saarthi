"""Soundbox and other merchant-facing notifications.

A Soundbox is a speaker on the counter that says "Paytm par ₹500 received". It
is how a small merchant knows a payment landed, and it is the single most
trusted signal in their day — which is exactly why it needs its own module
rather than being folded into the ledger.

The rule this whole file exists to enforce:

    A notification is evidence. The ledger is the fact.

`reconcile` is the only way to cross from one to the other, and it always
re-reads authoritative state to do it. It never writes: a merchant insisting
they heard the Soundbox cannot cause a transaction to exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import (
    NotificationChannel,
    NotificationKind,
    PaymentStatus,
    ReconciliationOutcome,
)
from ..database.ids import next_id
from ..database.models import NotificationEvent, Transaction


@dataclass
class Reconciliation:
    """What the ledger says about one thing the merchant was told."""

    notification_id: str
    reference: str
    announced_amount: Decimal | None
    announced_at: datetime
    outcome: ReconciliationOutcome
    transaction_id: str | None
    ledger_amount: Decimal | None
    payment_status: str | None
    explanation: str

    @property
    def confirmed(self) -> bool:
        return self.outcome == ReconciliationOutcome.MATCHED_SUCCESS

    def as_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "reference": self.reference,
            "announced_amount": str(self.announced_amount) if self.announced_amount else None,
            "announced_at": self.announced_at.isoformat(),
            "outcome": self.outcome.value,
            "transaction_id": self.transaction_id,
            "ledger_amount": str(self.ledger_amount) if self.ledger_amount else None,
            "payment_status": self.payment_status,
            "explanation": self.explanation,
            "confirmed_by_ledger": self.confirmed,
            # Stated in the payload itself, so nothing downstream can read a
            # notification as though it were a payment record.
            "authoritative": False,
        }


async def record_announcement(
    session: AsyncSession,
    *,
    merchant_id: str,
    reference: str = "",
    announced_amount: Decimal | None = None,
    channel: NotificationChannel = NotificationChannel.SOUNDBOX,
    kind: NotificationKind = NotificationKind.PAYMENT_ANNOUNCED,
    device_id: str = "",
    announced_at: datetime | None = None,
    meta: dict | None = None,
) -> NotificationEvent:
    event = NotificationEvent(
        id=await next_id(session, "notification"),
        merchant_id=merchant_id,
        channel=channel,
        kind=kind,
        device_id=device_id,
        reference=reference,
        announced_amount=announced_amount,
        meta=meta or {},
    )
    if announced_at is not None:
        event.announced_at = announced_at
    session.add(event)
    await session.flush()
    return event


async def list_announcements(
    session: AsyncSession,
    merchant_id: str,
    *,
    since: datetime | None = None,
    kind: NotificationKind | None = None,
    limit: int = 20,
) -> list[NotificationEvent]:
    stmt = (
        select(NotificationEvent)
        .where(NotificationEvent.merchant_id == merchant_id)
        .order_by(NotificationEvent.announced_at.desc())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(NotificationEvent.announced_at >= since)
    if kind is not None:
        stmt = stmt.where(NotificationEvent.kind == kind)
    rows = await session.scalars(stmt)
    return list(rows)


async def get_announcement(session: AsyncSession, notification_id: str) -> NotificationEvent | None:
    return await session.get(NotificationEvent, notification_id)


async def reconcile(
    session: AsyncSession, notification: NotificationEvent
) -> Reconciliation:
    """Ask the ledger about one announcement. Read-only, always.

    The reference is looked up rather than trusted, and it is scoped to the
    merchant who heard it: a device must not be able to surface another
    merchant's transaction by announcing its id.
    """
    txn: Transaction | None = None
    if notification.reference:
        txn = await session.scalar(
            select(Transaction).where(
                Transaction.id == notification.reference,
                Transaction.merchant_id == notification.merchant_id,
            )
        )

    if txn is None:
        return _result(
            notification,
            ReconciliationOutcome.NO_AUTHORITATIVE_RECORD,
            None,
            "The ledger has no transaction matching what the device announced. "
            "There is no payment to act on.",
        )

    if txn.payment_status == PaymentStatus.FAILED:
        return _result(
            notification,
            ReconciliationOutcome.MATCHED_FAILED,
            txn,
            f"{txn.id} exists but the payment failed. The announcement was premature.",
        )

    if txn.payment_status == PaymentStatus.PAYMENT_PENDING:
        return _result(
            notification,
            ReconciliationOutcome.MATCHED_PENDING,
            txn,
            f"{txn.id} exists but is still pending confirmation from the bank.",
        )

    if (
        notification.announced_amount is not None
        and Decimal(notification.announced_amount) != Decimal(txn.amount)
    ):
        return _result(
            notification,
            ReconciliationOutcome.AMOUNT_MISMATCH,
            txn,
            f"{txn.id} succeeded for ₹{txn.amount}, but ₹{notification.announced_amount} "
            "was announced.",
        )

    return _result(
        notification,
        ReconciliationOutcome.MATCHED_SUCCESS,
        txn,
        f"{txn.id} succeeded for ₹{txn.amount}. The announcement was correct.",
    )


async def reconcile_recent(
    session: AsyncSession,
    merchant_id: str,
    *,
    since: datetime | None = None,
    limit: int = 20,
) -> list[Reconciliation]:
    announcements = await list_announcements(session, merchant_id, since=since, limit=limit)
    return [await reconcile(session, event) for event in announcements]


def _result(
    notification: NotificationEvent,
    outcome: ReconciliationOutcome,
    txn: Transaction | None,
    explanation: str,
) -> Reconciliation:
    return Reconciliation(
        notification_id=notification.id,
        reference=notification.reference,
        announced_amount=notification.announced_amount,
        announced_at=notification.announced_at,
        outcome=outcome,
        transaction_id=txn.id if txn else None,
        ledger_amount=txn.amount if txn else None,
        payment_status=txn.payment_status.value if txn else None,
        explanation=explanation,
    )
