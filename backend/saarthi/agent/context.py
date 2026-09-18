"""Context engine.

Builds the full picture for a case: current authoritative state from Postgres
plus advisory semantic memory. The separation is deliberate and is preserved
all the way to the UI: memory informs, the database decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import Case
from ..services import ledger_service, ops_service, refund_service


@dataclass
class CaseContext:
    case_id: str
    merchant: dict[str, Any]
    transaction: dict[str, Any] | None = None
    payment_history: list[dict] = field(default_factory=list)
    settlement: dict[str, Any] | None = None
    settlement_eta: dict[str, Any] | None = None
    disputes: list[dict] = field(default_factory=list)
    refunds: list[dict] = field(default_factory=list)
    tickets: list[dict] = field(default_factory=list)
    merchant_history: dict[str, Any] = field(default_factory=dict)
    memory: dict[str, Any] | None = None

    def for_prompt(self) -> dict:
        """The subset handed to the model. Memory is explicitly marked advisory."""
        return {
            "merchant": self.merchant,
            "transaction": self.transaction,
            "settlement": self.settlement,
            "settlement_eta": self.settlement_eta,
            "disputes": self.disputes,
            "refunds": self.refunds,
            "payment_history": self.payment_history,
            "merchant_history": self.merchant_history,
            "memory_advisory_only": (self.memory or {}).get("similar_cases", []),
        }


async def build_context(
    session: AsyncSession, case: Case, *, memory: dict | None = None
) -> CaseContext:
    merchant = await ledger_service.get_merchant(session, case.merchant_id)
    ctx = CaseContext(
        case_id=case.id,
        merchant={
            "id": merchant.id,
            "name": merchant.name,
            "risk_level": merchant.risk_level.value,
            "autonomous_refund_limit": str(merchant.autonomous_refund_limit),
            "currency": merchant.currency,
        },
        memory=memory,
    )

    history = await ledger_service.get_merchant_history(session, case.merchant_id)
    by_intent: dict[str, int] = {}
    for prior in history:
        if prior.intent:
            by_intent[prior.intent] = by_intent.get(prior.intent, 0) + 1
    ctx.merchant_history = {
        "previous_case_count": len(history),
        "by_intent": by_intent,
        "last_case_at": history[0].resolved_at.isoformat() if history and history[0].resolved_at else None,
    }

    if not case.transaction_id:
        return ctx

    txn = await ledger_service.get_transaction(session, case.transaction_id)
    ctx.transaction = {
        "id": txn.id,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "payment_status": txn.payment_status.value,
        "customer_debited": txn.customer_debited,
        "customer_reference": txn.customer_reference,
        "description": txn.description,
        "refunded_amount": str(txn.refunded_amount),
        "created_at": txn.created_at.isoformat(),
    }

    events = await ledger_service.get_payment_history(session, case.transaction_id)
    ctx.payment_history = [
        {"kind": e.kind, "detail": e.detail, "occurred_at": e.occurred_at.isoformat()} for e in events
    ]

    try:
        stl = await ledger_service.get_settlement(session, case.transaction_id)
        eta = await ledger_service.get_settlement_eta(session, case.transaction_id)
        ctx.settlement = {
            "id": stl.id,
            "status": stl.status.value,
            "expected_at": stl.expected_at.isoformat() if stl.expected_at else None,
            "completed_at": stl.completed_at.isoformat() if stl.completed_at else None,
            "delay_reason": stl.delay_reason,
        }
        ctx.settlement_eta = {
            "eta_seconds": eta.eta_seconds,
            "overdue": eta.overdue,
            "expected_at": eta.expected_at.isoformat() if eta.expected_at else None,
        }
    except Exception:  # noqa: BLE001 - a transaction may have no settlement record
        ctx.settlement = None

    disputes = await ledger_service.get_disputes(session, case.transaction_id)
    ctx.disputes = [
        {
            "id": d.id,
            "type": d.type.value,
            "status": d.status.value,
            "description": d.description,
            "requested_amount": str(d.requested_amount) if d.requested_amount else None,
            "evidence": d.evidence,
        }
        for d in disputes
    ]

    refunds = await refund_service.list_refunds_for_transaction(session, case.transaction_id)
    ctx.refunds = [
        {
            "id": r.id,
            "amount": str(r.amount),
            "status": r.status.value,
            "attempt_count": r.attempt_count,
            "idempotency_key": r.idempotency_key,
            "scheduled_for": r.scheduled_for.isoformat() if r.scheduled_for else None,
        }
        for r in refunds
    ]

    messages = await ops_service.list_messages(session, case.id)
    ctx.tickets = []
    _ = messages
    return ctx
