"""SQLAlchemy models.

Notes for maintainers:
  * `metadata` is reserved on DeclarativeBase, so audit metadata is mapped as
    `meta` with column name "metadata".
  * JSON columns are NOT mutation-tracked. Always reassign a new dict rather
    than mutating in place, or the change is silently not persisted.
  * Money is Numeric(12, 2) -> Decimal. Never compare against floats.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base, JSONType, TZDateTime, portable_enum, utcnow
from .enums import (
    ActionStatus,
    Actor,
    CaseOrigin,
    CaseOwner,
    CaseStatus,
    DisputeStatus,
    DisputeType,
    EscalationStatus,
    EventStatus,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    PaymentStatus,
    RefundStatus,
    Resolution,
    RiskLevel,
    SettlementStatus,
    TicketPriority,
    TicketStatus,
    WorkflowStatus,
)


class Counter(Base):
    __tablename__ = "counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Merchant(Base):
    __tablename__ = "merchants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    risk_level: Mapped[RiskLevel] = mapped_column(portable_enum(RiskLevel), default=RiskLevel.LOW)
    autonomous_refund_limit: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("5000.00"))
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    meta: Mapped[dict] = mapped_column("metadata", JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    payment_status: Mapped[PaymentStatus] = mapped_column(portable_enum(PaymentStatus))
    customer_debited: Mapped[bool] = mapped_column(Boolean, default=False)
    customer_reference: Mapped[str] = mapped_column(String(80), default="")
    description: Mapped[str] = mapped_column(String(300), default="")
    refunded_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    settlement: Mapped[Settlement | None] = relationship(back_populates="transaction", uselist=False)


class TransactionEvent(Base):
    """Backs the payment-history and transaction-history endpoints."""

    __tablename__ = "transaction_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Settlement(Base):
    __tablename__ = "settlements"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), unique=True, index=True)
    status: Mapped[SettlementStatus] = mapped_column(portable_enum(SettlementStatus))
    expected_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    delay_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    monitor_armed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)

    transaction: Mapped[Transaction] = relationship(back_populates="settlement")


class Dispute(Base):
    __tablename__ = "disputes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    type: Mapped[DisputeType] = mapped_column(portable_enum(DisputeType))
    description: Mapped[str] = mapped_column(Text, default="")
    requested_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[DisputeStatus] = mapped_column(portable_enum(DisputeStatus), default=DisputeStatus.OPEN)
    evidence: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Case(Base):
    __tablename__ = "cases"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(ForeignKey("merchants.id"), index=True)
    transaction_id: Mapped[str | None] = mapped_column(ForeignKey("transactions.id"), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[CaseStatus] = mapped_column(portable_enum(CaseStatus), default=CaseStatus.RECEIVED)
    origin: Mapped[CaseOrigin] = mapped_column(portable_enum(CaseOrigin), default=CaseOrigin.MERCHANT_CHAT)
    owner: Mapped[CaseOwner] = mapped_column(portable_enum(CaseOwner), default=CaseOwner.SAARTHI)
    diagnosis: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk: Mapped[RiskLevel | None] = mapped_column(portable_enum(RiskLevel), nullable=True)
    requires_human: Mapped[bool] = mapped_column(Boolean, default=False)
    # Set when policy MANDATES human review. Excluded from the autonomy-rate
    # denominator: correct escalation must not look like a failure.
    human_required_by_policy: Mapped[bool] = mapped_column(Boolean, default=False)
    original_message: Mapped[str] = mapped_column(Text, default="")
    plan: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    plan_cursor: Mapped[int] = mapped_column(Integer, default=0)
    policy_decisions: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    current_action: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    wait_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    human_override: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    resolution: Mapped[Resolution | None] = mapped_column(portable_enum(Resolution), nullable=True)
    scenario: Mapped[str | None] = mapped_column(String(40), nullable=True)
    next_sequence: Mapped[int] = mapped_column(Integer, default=0)
    first_action_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class Refund(Base):
    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    transaction_id: Mapped[str] = mapped_column(ForeignKey("transactions.id"), index=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[RefundStatus] = mapped_column(portable_enum(RefundStatus))
    reason: Mapped[str] = mapped_column(String(200), default="")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    # Unique constraint is the hard backstop against double refunds.
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    scheduled_condition: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    status: Mapped[TicketStatus] = mapped_column(portable_enum(TicketStatus), default=TicketStatus.OPEN)
    priority: Mapped[TicketPriority] = mapped_column(
        portable_enum(TicketPriority), default=TicketPriority.NORMAL
    )
    subject: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


class Policy(Base):
    __tablename__ = "policies"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    policy_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    configuration: Mapped[dict] = mapped_column(JSONType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)


class Action(Base):
    __tablename__ = "actions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[ActionStatus] = mapped_column(portable_enum(ActionStatus))
    input: Mapped[dict] = mapped_column(JSONType, default=dict)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    error: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    policy_decision: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class AgentEvent(Base):
    __tablename__ = "agent_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    actor: Mapped[Actor] = mapped_column(portable_enum(Actor), default=Actor.SAARTHI)
    status: Mapped[EventStatus] = mapped_column(portable_enum(EventStatus), default=EventStatus.SUCCESS)
    message: Mapped[str] = mapped_column(Text, default="")
    input: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    risk: Mapped[RiskLevel | None] = mapped_column(portable_enum(RiskLevel), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    recommendation: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[EscalationStatus] = mapped_column(
        portable_enum(EscalationStatus), default=EscalationStatus.PENDING_HUMAN
    )
    assigned_to: Mapped[str | None] = mapped_column(String(160), nullable=True)
    human_decision: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    pending_action: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    completed_actions: Mapped[list] = mapped_column(JSONType, default=list)
    context_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    policy: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    resume_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    direction: Mapped[MessageDirection] = mapped_column(portable_enum(MessageDirection))
    channel: Mapped[MessageChannel] = mapped_column(
        portable_enum(MessageChannel), default=MessageChannel.CHAT
    )
    sender: Mapped[Actor] = mapped_column(portable_enum(Actor))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[MessageStatus] = mapped_column(portable_enum(MessageStatus), default=MessageStatus.DRAFT)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class MemoryDocument(Base):
    """Search corpus and ingestion outbox in one table.

    The embedding column is only populated when a real embedding provider is
    configured; the keyword index works from `body` regardless.
    """

    __tablename__ = "memory_documents"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # case | knowledge
    ref_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    merchant_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    header: Mapped[dict] = mapped_column(JSONType, default=dict)
    tags: Mapped[list] = mapped_column(JSONType, default=list)
    embedded_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    workflow: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    engine: Mapped[str] = mapped_column(String(20), nullable=False)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    status: Mapped[WorkflowStatus] = mapped_column(
        portable_enum(WorkflowStatus), default=WorkflowStatus.DISPATCHED
    )
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    state: Mapped[dict] = mapped_column(JSONType, default=dict)
    external_execution_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resume_url: Mapped[str | None] = mapped_column(String(400), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_check_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)


class ProactiveAlert(Base):
    __tablename__ = "proactive_alerts"
    __table_args__ = (UniqueConstraint("transaction_id", "case_id", name="uq_alert_txn_case"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    merchant_id: Mapped[str] = mapped_column(String(40), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(60), nullable=False)
    expected_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    overdue_seconds: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
