"""Structured outputs and internal agent types."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..database.enums import RiskLevel


class Intent(StrEnum):
    PAYMENT_FAILED = "PAYMENT_FAILED"
    PAYMENT_DEBITED_BUT_NOT_CONFIRMED = "PAYMENT_DEBITED_BUT_NOT_CONFIRMED"
    SETTLEMENT_DELAY = "SETTLEMENT_DELAY"
    REFUND_REQUEST = "REFUND_REQUEST"
    REFUND_STATUS = "REFUND_STATUS"
    PRODUCT_QUALITY_DISPUTE = "PRODUCT_QUALITY_DISPUTE"
    HIGH_VALUE_REFUND = "HIGH_VALUE_REFUND"
    GENERAL_TRANSACTION_QUERY = "GENERAL_TRANSACTION_QUERY"
    UNKNOWN = "UNKNOWN"


class RootCause(StrEnum):
    SETTLEMENT_DELAY = "SETTLEMENT_DELAY"
    PAYMENT_FAILED_CONFIRMED = "PAYMENT_FAILED_CONFIRMED"
    CUSTOMER_REFUND_REQUEST = "CUSTOMER_REFUND_REQUEST"
    PRODUCT_QUALITY_DISPUTE = "PRODUCT_QUALITY_DISPUTE"
    REFUND_IN_PROGRESS = "REFUND_IN_PROGRESS"
    NO_ISSUE_FOUND = "NO_ISSUE_FOUND"
    UNKNOWN = "UNKNOWN"


class Diagnosis(BaseModel):
    intent: Intent
    transaction_id: str | None = None
    root_cause: RootCause
    confidence: float = Field(ge=0.0, le=1.0)
    risk: RiskLevel
    requires_human: bool = False
    merchant_requests_human: bool = False
    requested_amount: Decimal | None = None
    summary: str = Field(default="", max_length=400)
    evidence: list[str] = Field(default_factory=list)
    # Real providers may fill this. It is validated against the tool registry,
    # logged for transparency, and never executed.
    suggested_actions: list[str] = Field(default_factory=list)
    clamped_fields: list[str] = Field(default_factory=list)


class MessageDraft(BaseModel):
    body: str = Field(max_length=900)
    tone: Literal["reassuring", "informational", "apologetic"] = "informational"
    claims: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    purpose: str = ""
    is_primary: bool = False
    is_retry: bool = False
    attempt: int = 1


class GoalCondition(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class Plan(BaseModel):
    goal: str
    steps: list[ProposedAction] = Field(default_factory=list)
    goal_conditions: list[GoalCondition] = Field(default_factory=list)
    primary_step: int | None = None
    trigger: str = "NEW_MESSAGE"


class PolicyDecisionType(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class HumanOverride(BaseModel):
    escalation_id: str
    decided_by: str
    decided_at: datetime
    note: str | None = None


class PolicyRuleResult(BaseModel):
    policy_id: str
    policy_type: str
    decision: PolicyDecisionType
    reason: str


class PolicyDecision(BaseModel):
    decision: PolicyDecisionType
    reasons: list[str] = Field(default_factory=list)
    policy_ids: list[str] = Field(default_factory=list)
    evaluated: list[PolicyRuleResult] = Field(default_factory=list)
    human_override: HumanOverride | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == PolicyDecisionType.ALLOW


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    PENDING = "PENDING"
    FAILED = "FAILED"
    CONDITION_TRIGGERED = "CONDITION_TRIGGERED"


class CheckResult(BaseModel):
    kind: str
    status: VerificationStatus
    observed: dict[str, Any] = Field(default_factory=dict)
    expected: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
    condition: str | None = None


class VerificationResult(BaseModel):
    status: VerificationStatus
    checks: list[CheckResult] = Field(default_factory=list)
    condition: str | None = None


class FailureClass(StrEnum):
    TRANSIENT_API_ERROR = "TRANSIENT_API_ERROR"
    STATE_CONFLICT = "STATE_CONFLICT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    UNKNOWN = "UNKNOWN"


class SideEffectStatus(StrEnum):
    APPLIED = "APPLIED"
    NOT_APPLIED = "NOT_APPLIED"
    UNKNOWN = "UNKNOWN"


class SideEffectCheck(BaseModel):
    status: SideEffectStatus
    evidence: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class RecoveryDecision(StrEnum):
    RETRY = "RETRY"
    SIDE_EFFECT_ALREADY_APPLIED = "SIDE_EFFECT_ALREADY_APPLIED"
    ALTERNATIVE = "ALTERNATIVE"
    ESCALATE = "ESCALATE"


class RecoveryPlan(BaseModel):
    decision: RecoveryDecision
    reason: str
    failure_class: FailureClass
    side_effect: SideEffectCheck
    next_attempt: int | None = None
    alternative: ProposedAction | None = None
    escalation_reason: str | None = None


class EscalationReason(StrEnum):
    HIGH_VALUE_SUBJECTIVE_DISPUTE = "HIGH_VALUE_SUBJECTIVE_DISPUTE"
    HIGH_VALUE_REFUND = "HIGH_VALUE_REFUND"
    SUBJECTIVE_DISPUTE = "SUBJECTIVE_DISPUTE"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    MERCHANT_REQUESTED_HUMAN = "MERCHANT_REQUESTED_HUMAN"
    RISK_TOO_HIGH = "RISK_TOO_HIGH"
    IDENTIFICATION_FAILED = "IDENTIFICATION_FAILED"
    CONTEXT_UNAVAILABLE = "CONTEXT_UNAVAILABLE"
    NO_PERMITTED_PLAN = "NO_PERMITTED_PLAN"
    RECOVERY_EXHAUSTED = "RECOVERY_EXHAUSTED"
    SIDE_EFFECT_UNKNOWN = "SIDE_EFFECT_UNKNOWN"
    STATE_CONFLICT = "STATE_CONFLICT"
    RETRY_DENIED = "RETRY_DENIED"
    SENSITIVE_ACTION = "SENSITIVE_ACTION"


# Escalation reasons where a human was REQUIRED by policy rather than the agent
# failing. These are excluded from the autonomy-rate denominator.
POLICY_MANDATED_REASONS = frozenset(
    {
        EscalationReason.HIGH_VALUE_SUBJECTIVE_DISPUTE,
        EscalationReason.HIGH_VALUE_REFUND,
        EscalationReason.SUBJECTIVE_DISPUTE,
        EscalationReason.MERCHANT_REQUESTED_HUMAN,
        EscalationReason.SENSITIVE_ACTION,
    }
)


class Recommendation(StrEnum):
    REVIEW_PARTIAL_REFUND = "REVIEW_PARTIAL_REFUND"
    APPROVE_REFUND = "APPROVE_REFUND"
    MANUAL_REFUND_REVIEW = "MANUAL_REFUND_REVIEW"
    CLARIFY_WITH_MERCHANT = "CLARIFY_WITH_MERCHANT"
    MANUAL_INVESTIGATION = "MANUAL_INVESTIGATION"
