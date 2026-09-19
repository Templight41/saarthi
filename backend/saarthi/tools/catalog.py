"""The tool catalogue.

Every controlled action the agent can take. Read tools are free; side-effecting
tools carry `requires_policy=True` and the executor refuses to run them without
an explicit ALLOW.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from ..agent.events import EventType
from ..database.enums import DisputeStatus, DisputeType, MessageChannel, TicketPriority, TicketStatus
from ..services import ledger_service, notification_service, ops_service, refund_service
from .registry import ToolContext, ToolRegistry, ToolSpec

registry = ToolRegistry()


# --------------------------------------------------------------------------
# Input models
# --------------------------------------------------------------------------
class TransactionArgs(BaseModel):
    transaction_id: str


class MerchantArgs(BaseModel):
    merchant_id: str


class RefundArgs(BaseModel):
    transaction_id: str
    amount: Decimal
    reason: str = ""


class ScheduleRefundArgs(BaseModel):
    transaction_id: str
    amount: Decimal
    reason: str = ""
    deadline: datetime
    condition_type: str = "SETTLEMENT_NOT_COMPLETED_BY"


class RefundIdArgs(BaseModel):
    refund_id: str
    reason: str = ""


class TicketArgs(BaseModel):
    subject: str
    description: str = ""
    priority: TicketPriority = TicketPriority.NORMAL


class TicketUpdateArgs(BaseModel):
    ticket_id: str
    status: TicketStatus


class DraftMessageArgs(BaseModel):
    stage: str = "OUTCOME"
    facts: dict = Field(default_factory=dict)
    channel: MessageChannel = MessageChannel.CHAT


class SendMessageArgs(BaseModel):
    message_id: str


class CreateDisputeArgs(BaseModel):
    transaction_id: str
    type: DisputeType = DisputeType.OTHER
    description: str = ""
    requested_amount: Decimal | None = None


class UpdateDisputeArgs(BaseModel):
    dispute_id: str
    status: DisputeStatus


# --------------------------------------------------------------------------
# Read tools
# --------------------------------------------------------------------------
async def _get_transaction(ctx: ToolContext, args: TransactionArgs) -> dict:
    txn = await ledger_service.get_transaction(ctx.session, args.transaction_id)
    return {
        "id": txn.id,
        "merchant_id": txn.merchant_id,
        "amount": str(txn.amount),
        "currency": txn.currency,
        "payment_status": txn.payment_status.value,
        "customer_debited": txn.customer_debited,
        "customer_reference": txn.customer_reference,
        "description": txn.description,
        "refunded_amount": str(txn.refunded_amount),
    }


async def _get_payment_history(ctx: ToolContext, args: TransactionArgs) -> dict:
    events = await ledger_service.get_payment_history(ctx.session, args.transaction_id)
    return {
        "events": [
            {"kind": e.kind, "detail": e.detail, "occurred_at": e.occurred_at.isoformat()} for e in events
        ]
    }


async def _get_settlement_status(ctx: ToolContext, args: TransactionArgs) -> dict:
    stl = await ledger_service.get_settlement(ctx.session, args.transaction_id)
    return {
        "id": stl.id,
        "status": stl.status.value,
        "expected_at": stl.expected_at.isoformat() if stl.expected_at else None,
        "completed_at": stl.completed_at.isoformat() if stl.completed_at else None,
        "delay_reason": stl.delay_reason,
    }


async def _get_settlement_eta(ctx: ToolContext, args: TransactionArgs) -> dict:
    eta = await ledger_service.get_settlement_eta(ctx.session, args.transaction_id)
    return {
        "status": eta.status.value,
        "expected_at": eta.expected_at.isoformat() if eta.expected_at else None,
        "eta_seconds": eta.eta_seconds,
        "overdue": eta.overdue,
        "delay_reason": eta.delay_reason,
    }


async def _get_dispute(ctx: ToolContext, args: TransactionArgs) -> dict:
    disputes = await ledger_service.get_disputes(ctx.session, args.transaction_id)
    return {
        "count": len(disputes),
        "disputes": [
            {
                "id": d.id,
                "type": d.type.value,
                "status": d.status.value,
                "description": d.description,
                "requested_amount": str(d.requested_amount) if d.requested_amount else None,
                "evidence": d.evidence,
            }
            for d in disputes
        ],
    }


async def _get_merchant(ctx: ToolContext, args: MerchantArgs) -> dict:
    m = await ledger_service.get_merchant(ctx.session, args.merchant_id)
    return {
        "id": m.id,
        "name": m.name,
        "email": m.email,
        "risk_level": m.risk_level.value,
        "autonomous_refund_limit": str(m.autonomous_refund_limit),
        "currency": m.currency,
        "language": m.language,
    }


async def _get_merchant_history(ctx: ToolContext, args: MerchantArgs) -> dict:
    cases = await ledger_service.get_merchant_history(ctx.session, args.merchant_id)
    return {
        "previous_case_count": len(cases),
        "cases": [
            {
                "case_id": c.id,
                "intent": c.intent,
                "resolution": c.resolution.value if c.resolution else None,
                "resolved_at": c.resolved_at.isoformat() if c.resolved_at else None,
            }
            for c in cases
        ],
    }


async def _get_refund(ctx: ToolContext, args: RefundIdArgs) -> dict:
    refund = await refund_service.get_refund(ctx.session, args.refund_id)
    return _refund_dict(refund)


async def _get_soundbox_notifications(ctx: ToolContext, args: MerchantArgs) -> dict:
    """What the merchant's devices announced. Evidence, never payment state."""
    events = await notification_service.list_announcements(ctx.session, args.merchant_id)
    return {
        "count": len(events),
        "authoritative": False,
        "notifications": [
            {
                "id": e.id,
                "channel": e.channel.value,
                "kind": e.kind.value,
                "device_id": e.device_id,
                "reference": e.reference,
                "announced_amount": str(e.announced_amount) if e.announced_amount else None,
                "announced_at": e.announced_at.isoformat(),
            }
            for e in events
        ],
    }


async def _reconcile_notifications(ctx: ToolContext, args: MerchantArgs) -> dict:
    """Ask the ledger about each announcement. This is the only crossing from
    what the merchant was told to what is actually true."""
    results = await notification_service.reconcile_recent(ctx.session, args.merchant_id)
    return {
        "count": len(results),
        "unconfirmed": sum(1 for r in results if not r.confirmed),
        "reconciliations": [r.as_dict() for r in results],
    }


def _refund_dict(refund) -> dict:
    return {
        "id": refund.id,
        "transaction_id": refund.transaction_id,
        "amount": str(refund.amount),
        "status": refund.status.value,
        "reason": refund.reason,
        "attempt_count": refund.attempt_count,
        "idempotency_key": refund.idempotency_key,
        "scheduled_for": refund.scheduled_for.isoformat() if refund.scheduled_for else None,
        "completed_at": refund.completed_at.isoformat() if refund.completed_at else None,
    }


# --------------------------------------------------------------------------
# Side-effecting tools
# --------------------------------------------------------------------------
async def _issue_refund(ctx: ToolContext, args: RefundArgs) -> dict:
    refund = await refund_service.issue_refund(
        ctx.session,
        transaction_id=args.transaction_id,
        amount=args.amount,
        reason=args.reason,
        idempotency_key=ctx.case.id and _refund_key(ctx.case.id, args.transaction_id, args.amount),
        case_id=ctx.case.id,
        simulation=ctx.simulation,
    )
    return _refund_dict(refund)


async def _schedule_refund(ctx: ToolContext, args: ScheduleRefundArgs) -> dict:
    refund = await refund_service.schedule_refund(
        ctx.session,
        transaction_id=args.transaction_id,
        amount=args.amount,
        reason=args.reason,
        condition={"type": args.condition_type, "deadline": args.deadline.isoformat()},
        deadline=args.deadline,
        idempotency_key=_refund_key(ctx.case.id, args.transaction_id, args.amount),
        case_id=ctx.case.id,
    )
    return _refund_dict(refund)


async def _cancel_scheduled_refund(ctx: ToolContext, args: RefundIdArgs) -> dict:
    refund = await refund_service.cancel_scheduled_refund(ctx.session, args.refund_id, args.reason)
    return _refund_dict(refund)


async def _create_ticket(ctx: ToolContext, args: TicketArgs) -> dict:
    ticket = await ops_service.create_ticket(
        ctx.session,
        case_id=ctx.case.id,
        subject=args.subject,
        description=args.description,
        priority=args.priority,
        idempotency_key=f"ticket:{ctx.case.id}",
    )
    return {"id": ticket.id, "status": ticket.status.value, "subject": ticket.subject}


async def _update_ticket(ctx: ToolContext, args: TicketUpdateArgs) -> dict:
    ticket = await ops_service.update_ticket(ctx.session, args.ticket_id, status=args.status)
    return {"id": ticket.id, "status": ticket.status.value}


async def _draft_message(ctx: ToolContext, args: DraftMessageArgs) -> dict:
    """Drafting is read-only: it produces a DRAFT row that must still be sent.

    The claims guard lives here, so no provider can make an unverified claim.
    """
    from ..agent.messaging import draft_for_stage

    body, claims, language = await draft_for_stage(ctx, args.stage, args.facts)
    message = await ops_service.draft_message(
        ctx.session,
        case_id=ctx.case.id,
        content=body,
        channel=args.channel,
        meta={"stage": args.stage, "claims": claims, "language": language},
    )
    return {
        "id": message.id,
        "content": message.content,
        "claims": claims,
        "language": language,
    }


async def _send_message(ctx: ToolContext, args: SendMessageArgs) -> dict:
    message = await ops_service.send_message(ctx.session, args.message_id)
    return {"id": message.id, "status": message.status.value, "content": message.content}


async def _create_dispute(ctx: ToolContext, args: CreateDisputeArgs) -> dict:
    dispute = await ledger_service.create_dispute(
        ctx.session,
        transaction_id=args.transaction_id,
        type_=args.type,
        description=args.description,
        requested_amount=args.requested_amount,
    )
    return {"id": dispute.id, "status": dispute.status.value, "type": dispute.type.value}


async def _update_dispute(ctx: ToolContext, args: UpdateDisputeArgs) -> dict:
    dispute = await ledger_service.update_dispute(ctx.session, args.dispute_id, status=args.status)
    return {"id": dispute.id, "status": dispute.status.value}


def _refund_key(case_id: str, transaction_id: str, amount: Decimal) -> str:
    """Deterministic by construction, so a retry reuses the same key."""
    return f"refund:{case_id}:{transaction_id}:{Decimal(amount):.2f}"


def _refund_key_fn(case, args: dict) -> str | None:
    txn = args.get("transaction_id")
    amount = args.get("amount")
    if not txn or amount is None:
        return None
    return _refund_key(case.id, txn, Decimal(str(amount)))


def _ticket_key_fn(case, args: dict) -> str:
    return f"ticket:{case.id}"


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------
_READ_TOOLS = [
    ("get_transaction", "Read the current state of a transaction.", TransactionArgs, _get_transaction),
    ("get_payment_history", "Read the payment event history.", TransactionArgs, _get_payment_history),
    (
        "get_settlement_status",
        "Read the settlement record for a transaction.",
        TransactionArgs,
        _get_settlement_status,
    ),
    ("get_settlement_eta", "Read the settlement ETA.", TransactionArgs, _get_settlement_eta),
    ("get_dispute", "Read disputes raised on a transaction.", TransactionArgs, _get_dispute),
    ("get_merchant", "Read merchant profile and refund authority.", MerchantArgs, _get_merchant),
    (
        "get_merchant_history",
        "Read the merchant's previously resolved cases.",
        MerchantArgs,
        _get_merchant_history,
    ),
    ("get_refund", "Read a refund record.", RefundIdArgs, _get_refund),
    (
        "get_soundbox_notifications",
        "Read what the merchant's Soundbox and other devices announced. Evidence, not payment state.",
        MerchantArgs,
        _get_soundbox_notifications,
    ),
    (
        "reconcile_notifications",
        "Check each device announcement against authoritative payment state.",
        MerchantArgs,
        _reconcile_notifications,
    ),
]

for _name, _desc, _model, _handler in _READ_TOOLS:
    registry.register(
        ToolSpec(name=_name, description=_desc, input_model=_model, handler=_handler)
    )

registry.register(
    ToolSpec(
        name="issue_refund",
        description="Issue a refund against a transaction.",
        input_model=RefundArgs,
        handler=_issue_refund,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
        verify_with="refund",
        idempotency_key_fn=_refund_key_fn,
    )
)
registry.register(
    ToolSpec(
        name="schedule_refund",
        description="Schedule a standby refund conditional on settlement outcome.",
        input_model=ScheduleRefundArgs,
        handler=_schedule_refund,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
        verify_with="scheduled_refund",
        idempotency_key_fn=_refund_key_fn,
        event_on_success=EventType.REFUND_SCHEDULED,
    )
)
registry.register(
    ToolSpec(
        name="cancel_scheduled_refund",
        description="Cancel a standby refund that is no longer needed.",
        input_model=RefundIdArgs,
        handler=_cancel_scheduled_refund,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
        verify_with="cancelled_refund",
    )
)
registry.register(
    ToolSpec(
        name="create_ticket",
        description="Open an operations ticket for this case.",
        input_model=TicketArgs,
        handler=_create_ticket,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
        verify_with="ticket",
        idempotency_key_fn=_ticket_key_fn,
        event_on_success=EventType.TICKET_CREATED,
    )
)
registry.register(
    ToolSpec(
        name="update_ticket",
        description="Update an operations ticket.",
        input_model=TicketUpdateArgs,
        handler=_update_ticket,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
    )
)
registry.register(
    ToolSpec(
        name="draft_message",
        description="Draft a merchant-facing message. Subject to the claims guard.",
        input_model=DraftMessageArgs,
        handler=_draft_message,
        side_effecting=False,
        requires_policy=False,
        event_on_success=EventType.MESSAGE_DRAFTED,
    )
)
registry.register(
    ToolSpec(
        name="send_message",
        description="Send a drafted message to the merchant.",
        input_model=SendMessageArgs,
        handler=_send_message,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
        verify_with="message",
        event_on_success=EventType.MESSAGE_SENT,
    )
)
registry.register(
    ToolSpec(
        name="create_dispute",
        description="Record a dispute against a transaction.",
        input_model=CreateDisputeArgs,
        handler=_create_dispute,
        side_effecting=True,
        requires_policy=True,
        idempotent=False,
    )
)
registry.register(
    ToolSpec(
        name="update_dispute",
        description="Update the status of a dispute.",
        input_model=UpdateDisputeArgs,
        handler=_update_dispute,
        side_effecting=True,
        requires_policy=True,
        idempotent=True,
    )
)
