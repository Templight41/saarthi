"""Domain enums.

Every member's name equals its value, so SQLAlchemy's name-vs-value storage
distinction is irrelevant and DB strings match API strings exactly.
"""

from __future__ import annotations

from enum import StrEnum


class CaseStatus(StrEnum):
    RECEIVED = "RECEIVED"
    IDENTIFYING = "IDENTIFYING"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSING = "DIAGNOSING"
    POLICY_CHECK = "POLICY_CHECK"
    PLANNING = "PLANNING"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    ESCALATED = "ESCALATED"


class PaymentStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"


class SettlementStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DisputeType(StrEnum):
    PRODUCT_QUALITY = "PRODUCT_QUALITY"
    NOT_RECEIVED = "NOT_RECEIVED"
    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    FRAUD = "FRAUD"
    OTHER = "OTHER"


class DisputeStatus(StrEnum):
    OPEN = "OPEN"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    REJECTED = "REJECTED"


class RefundStatus(StrEnum):
    SCHEDULED = "SCHEDULED"
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TicketStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class ActionStatus(StrEnum):
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class EscalationStatus(StrEnum):
    PENDING_HUMAN = "PENDING_HUMAN"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    TAKEN_OVER = "TAKEN_OVER"


class MessageDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageStatus(StrEnum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    FAILED = "FAILED"


class MessageChannel(StrEnum):
    CHAT = "CHAT"
    VOICE = "VOICE"
    EMAIL = "EMAIL"
    SYSTEM = "SYSTEM"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class CaseOwner(StrEnum):
    SAARTHI = "SAARTHI"
    HUMAN = "HUMAN"


class CaseOrigin(StrEnum):
    MERCHANT_CHAT = "MERCHANT_CHAT"
    MERCHANT_VOICE = "MERCHANT_VOICE"
    PROACTIVE = "PROACTIVE"
    DEMO = "DEMO"


class Resolution(StrEnum):
    AUTONOMOUS = "AUTONOMOUS"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"


class Actor(StrEnum):
    SAARTHI = "SAARTHI"
    MERCHANT = "MERCHANT"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    N8N = "N8N"
    WORKFLOW = "WORKFLOW"


class EventStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    IN_PROGRESS = "IN_PROGRESS"
    INFO = "INFO"
    WARNING = "WARNING"


class WorkflowStatus(StrEnum):
    DISPATCHED = "DISPATCHED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
