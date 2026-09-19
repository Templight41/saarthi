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
    # Past its expected window. Still on its way, but late enough that the
    # monitor has noticed — a distinct fact from "not due yet", which is what
    # lets a proactive case say something true about why it opened.
    OVERDUE = "OVERDUE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


#: Settlement has not landed. Anywhere that used to mean `== PENDING` means
#: this, or an overdue settlement quietly stops counting as unsettled.
UNSETTLED_SETTLEMENT = frozenset({SettlementStatus.PENDING, SettlementStatus.OVERDUE})


class PaymentMethod(StrEnum):
    """How the customer paid. A Soundbox is a speaker attached to a QR stand,
    not a payment rail of its own — it announces what the rail reports."""

    QR = "QR"
    UPI = "UPI"
    CARD = "CARD"
    NETBANKING = "NETBANKING"
    WALLET = "WALLET"


class NotificationChannel(StrEnum):
    SOUNDBOX = "SOUNDBOX"
    SMS = "SMS"
    APP_PUSH = "APP_PUSH"


class NotificationKind(StrEnum):
    PAYMENT_ANNOUNCED = "PAYMENT_ANNOUNCED"
    SETTLEMENT_ANNOUNCED = "SETTLEMENT_ANNOUNCED"
    REFUND_ANNOUNCED = "REFUND_ANNOUNCED"


class ReconciliationOutcome(StrEnum):
    """What the ledger says about something the merchant was told.

    `NO_AUTHORITATIVE_RECORD` is the one that matters: the device announced a
    payment and the ledger has never heard of it. That is not a payment.
    """

    MATCHED_SUCCESS = "MATCHED_SUCCESS"
    MATCHED_PENDING = "MATCHED_PENDING"
    MATCHED_FAILED = "MATCHED_FAILED"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    NO_AUTHORITATIVE_RECORD = "NO_AUTHORITATIVE_RECORD"


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
