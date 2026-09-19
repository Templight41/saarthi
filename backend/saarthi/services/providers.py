"""The boundary a real payment platform would be implemented against.

    Saarthi → tool registry → these protocols → a payment platform

Today every one of them is satisfied by a module in `services/`, backed by
PostgreSQL. Nothing here is a Paytm integration, and none of it pretends to
be: it is the shape such an integration would have to take, written down so
that the seam is checkable rather than asserted. `tests/test_providers.py`
asserts each module still satisfies its protocol, so the boundary cannot rot
while nobody is looking.

Protocols rather than base classes because the implementations are *modules*,
not objects. That is the existing convention in this codebase — `ledger_service`
is imported and called directly — and structural typing lets the contract be
written down without rewriting the callers to hold an instance.

Two properties are load-bearing and would survive a real integration:

* **Reads take ids and return current state.** Nothing accepts a previous
  result as evidence, which is what lets `verification/` re-check independently.
* **Writes take an idempotency key.** The executor derives one from the case,
  transaction and amount, so a retry is the same request by construction
  rather than by discipline.

What a production adapter would additionally need is in `docs/PRODUCTION.md`;
it is a longer list than this file, and none of it is implemented here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class MerchantProvider(Protocol):
    """Who the merchant is, and what they are trusted to have done on their behalf."""

    async def get_merchant(self, session: AsyncSession, merchant_id: str) -> Any: ...

    async def get_merchant_history(
        self, session: AsyncSession, merchant_id: str, limit: int = 10
    ) -> list[Any]: ...


@runtime_checkable
class PaymentProvider(Protocol):
    """Authoritative payment state. The only thing entitled to say a payment happened."""

    async def get_transaction(self, session: AsyncSession, transaction_id: str) -> Any: ...

    async def get_payment_history(
        self, session: AsyncSession, transaction_id: str
    ) -> list[Any]: ...

    async def find_transactions_for_merchant(
        self, session: AsyncSession, merchant_id: str
    ) -> list[Any]: ...


@runtime_checkable
class SettlementProvider(Protocol):
    """When the money reaches the merchant's bank, and whether it is late."""

    async def get_settlement(self, session: AsyncSession, transaction_id: str) -> Any: ...

    async def get_settlement_eta(self, session: AsyncSession, transaction_id: str) -> Any: ...

    async def find_delayed_settlements(
        self, session: AsyncSession, grace_seconds: int
    ) -> list[dict]: ...


@runtime_checkable
class RefundProvider(Protocol):
    """The only side effect that moves money, so the only one with a key on every call."""

    async def get_refund(self, session: AsyncSession, refund_id: str) -> Any: ...

    async def find_refund_by_idempotency_key(
        self, session: AsyncSession, key: str
    ) -> Any | None: ...

    async def list_refunds_for_transaction(
        self, session: AsyncSession, transaction_id: str
    ) -> list[Any]: ...

    async def issue_refund(
        self,
        session: AsyncSession,
        *,
        transaction_id: str,
        amount: Decimal,
        reason: str = ...,
        idempotency_key: str | None = ...,
        case_id: str | None = ...,
        simulation: Any = ...,
    ) -> Any: ...


@runtime_checkable
class DisputeProvider(Protocol):
    async def get_disputes(self, session: AsyncSession, transaction_id: str) -> list[Any]: ...

    async def update_dispute(self, session: AsyncSession, dispute_id: str, *, status: Any) -> Any: ...


@runtime_checkable
class NotificationProvider(Protocol):
    """What the merchant was told — Soundbox, SMS, app push.

    Separate from `PaymentProvider` on purpose, and the separation is the
    point: a notification is evidence at the same level as the merchant's own
    account of events. `reconcile` is the only crossing, and it reads
    authoritative state rather than trusting the announcement.
    """

    async def list_announcements(
        self,
        session: AsyncSession,
        merchant_id: str,
        *,
        since: datetime | None = ...,
        kind: Any = ...,
        limit: int = ...,
    ) -> list[Any]: ...

    async def reconcile(self, session: AsyncSession, notification: Any) -> Any: ...


#: Each protocol and the module that satisfies it today. The conformance test
#: walks this, so adding a protocol without an implementation fails loudly.
IMPLEMENTATIONS: dict[str, tuple[type, str]] = {
    "merchants": (MerchantProvider, "saarthi.services.ledger_service"),
    "payments": (PaymentProvider, "saarthi.services.ledger_service"),
    "settlements": (SettlementProvider, "saarthi.services.ledger_service"),
    "refunds": (RefundProvider, "saarthi.services.refund_service"),
    "disputes": (DisputeProvider, "saarthi.services.ledger_service"),
    "notifications": (NotificationProvider, "saarthi.services.notification_service"),
}
