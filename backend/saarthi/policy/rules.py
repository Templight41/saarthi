"""Policy rules.

Each rule is independent and configuration-driven, loaded from the `policies`
table so limits are data rather than code. A rule returns None when it does
not apply to the proposed action.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import ClassVar

from ..database.enums import DisputeStatus, PaymentStatus, RefundStatus, SettlementStatus
from ..database.models import Dispute, Merchant, Refund, Settlement, Transaction
from ..schemas.agent import (
    Diagnosis,
    HumanOverride,
    PolicyDecisionType,
    PolicyRuleResult,
    ProposedAction,
    SideEffectCheck,
    SideEffectStatus,
)

REFUND_TOOLS = frozenset({"issue_refund", "schedule_refund"})


@dataclass
class PolicyContext:
    merchant: Merchant
    now: datetime
    transaction: Transaction | None = None
    settlement: Settlement | None = None
    disputes: list[Dispute] = field(default_factory=list)
    diagnosis: Diagnosis | None = None
    existing_refunds: list[Refund] = field(default_factory=list)
    side_effect_check: SideEffectCheck | None = None
    human_override: HumanOverride | None = None
    failure_class: str | None = None


def _amount_of(action: ProposedAction) -> Decimal | None:
    raw = action.args.get("amount")
    if raw is None:
        return None
    return Decimal(str(raw)).quantize(Decimal("0.01"))


def _fmt(amount: Decimal) -> str:
    return f"₹{amount:,.0f}" if amount == amount.to_integral_value() else f"₹{amount:,.2f}"


class PolicyRule(ABC):
    policy_type: ClassVar[str]
    applies_to: ClassVar[frozenset[str]]
    overridable_by_human: ClassVar[bool] = True

    def __init__(self, policy_id: str, configuration: dict) -> None:
        self.policy_id = policy_id
        self.configuration = configuration

    @abstractmethod
    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None: ...

    def _result(self, decision: PolicyDecisionType, reason: str) -> PolicyRuleResult:
        return PolicyRuleResult(
            policy_id=self.policy_id,
            policy_type=self.policy_type,
            decision=decision,
            reason=reason,
        )


class RefundLimitPolicy(PolicyRule):
    policy_type = "REFUND_LIMIT"
    applies_to = REFUND_TOOLS

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if action.tool not in self.applies_to:
            return None
        amount = _amount_of(action)
        if amount is None:
            return None
        limit = Decimal(str(self.configuration.get("default_limit", 5000)))
        if self.configuration.get("use_merchant_limit", True):
            limit = Decimal(ctx.merchant.autonomous_refund_limit)
        if amount > limit:
            return self._result(
                PolicyDecisionType.REQUIRES_APPROVAL,
                f"{_fmt(amount)} exceeds the autonomous refund limit of {_fmt(limit)}",
            )
        return self._result(
            PolicyDecisionType.ALLOW,
            f"{_fmt(amount)} is within the autonomous refund limit of {_fmt(limit)}",
        )


class SubjectiveDisputePolicy(PolicyRule):
    policy_type = "SUBJECTIVE_DISPUTE"
    applies_to = REFUND_TOOLS | {"update_dispute"}

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if action.tool not in self.applies_to:
            return None
        subjective_types = set(self.configuration.get("subjective_types", []))
        subjective_intents = set(self.configuration.get("subjective_intents", []))

        open_subjective = [
            d
            for d in ctx.disputes
            if d.type.value in subjective_types
            and d.status in {DisputeStatus.OPEN, DisputeStatus.UNDER_REVIEW}
        ]
        if open_subjective:
            return self._result(
                PolicyDecisionType.REQUIRES_APPROVAL,
                f"Open {open_subjective[0].type.value.replace('_', '-').lower()} dispute "
                "requires human judgement",
            )
        if ctx.diagnosis and ctx.diagnosis.intent.value in subjective_intents:
            return self._result(
                PolicyDecisionType.REQUIRES_APPROVAL,
                "Product-quality disputes are subjective and require human review",
            )
        return None


class TransactionStatePolicy(PolicyRule):
    """Not overridable: a human cannot approve refunding an invalid transaction."""

    policy_type = "TRANSACTION_STATE"
    applies_to = REFUND_TOOLS | {"cancel_scheduled_refund"}
    overridable_by_human = False

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if action.tool not in self.applies_to:
            return None
        if action.tool == "cancel_scheduled_refund":
            return self._result(PolicyDecisionType.ALLOW, "Cancelling a standby refund is always safe")

        txn = ctx.transaction
        if txn is None:
            return self._result(PolicyDecisionType.DENY, "No transaction identified for this refund")

        amount = _amount_of(action)
        refundable_statuses = set(self.configuration.get("refundable_statuses", ["SUCCESS"]))
        schedule_allowed = set(self.configuration.get("schedule_allowed_statuses", ["PAYMENT_PENDING"]))
        settlement_states = set(self.configuration.get("pending_refund_requires_settlement", ["FAILED"]))

        if action.tool == "schedule_refund":
            if txn.payment_status.value in schedule_allowed and txn.customer_debited:
                return self._result(
                    PolicyDecisionType.ALLOW,
                    "Customer debit is confirmed, so a standby refund may be scheduled "
                    "while settlement is pending",
                )
            if txn.payment_status.value in refundable_statuses:
                return self._result(PolicyDecisionType.ALLOW, "Transaction is settled and refundable")
            return self._result(
                PolicyDecisionType.DENY,
                f"Transaction is {txn.payment_status.value} and cannot have a refund scheduled",
            )

        # issue_refund
        if txn.payment_status == PaymentStatus.REFUNDED:
            return self._result(PolicyDecisionType.DENY, "Transaction has already been fully refunded")

        already_completed = any(
            r.status == RefundStatus.COMPLETED and Decimal(r.amount) == (amount or Decimal("-1"))
            for r in ctx.existing_refunds
        )
        if already_completed:
            return self._result(
                PolicyDecisionType.DENY, "A completed refund for this amount already exists"
            )

        if amount is not None:
            remaining = Decimal(txn.amount) - Decimal(txn.refunded_amount)
            if amount > remaining:
                return self._result(
                    PolicyDecisionType.DENY,
                    f"{_fmt(amount)} exceeds the {_fmt(remaining)} still refundable",
                )

        if txn.payment_status.value in refundable_statuses:
            return self._result(PolicyDecisionType.ALLOW, "Transaction state is valid for a refund")

        settlement_failed = (
            ctx.settlement is not None and ctx.settlement.status.value in settlement_states
        )
        if txn.payment_status == PaymentStatus.PAYMENT_PENDING and txn.customer_debited:
            if settlement_failed:
                return self._result(
                    PolicyDecisionType.ALLOW,
                    "Settlement failed and the customer was debited, so a refund is owed",
                )
            return self._result(
                PolicyDecisionType.DENY,
                "Settlement outcome is not yet known; refunding now risks paying twice",
            )

        return self._result(
            PolicyDecisionType.DENY,
            f"Invalid transaction state {txn.payment_status.value} for a refund",
        )


class PendingPaymentPolicy(PolicyRule):
    """Investigate settlement before declaring failure.

    A deny here is what makes the planner fall back to a scheduled refund.
    """

    policy_type = "PENDING_PAYMENT"
    applies_to = frozenset({"issue_refund"})

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if action.tool not in self.applies_to:
            return None
        txn, stl = ctx.transaction, ctx.settlement
        if txn is None or stl is None:
            return None
        if txn.payment_status != PaymentStatus.PAYMENT_PENDING:
            return None
        if stl.status != SettlementStatus.PENDING:
            return None

        grace = timedelta(minutes=int(self.configuration.get("grace_minutes", 30)))
        deadline = (stl.expected_at + grace) if stl.expected_at else None
        if deadline is not None and ctx.now < deadline:
            return self._result(
                PolicyDecisionType.DENY,
                "Settlement is still within its expected window; investigate before "
                "declaring failure and use a scheduled refund instead",
            )
        return None


class RetryPolicy(PolicyRule):
    policy_type = "RETRY"
    applies_to = frozenset()  # applies by is_retry, not by tool name

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if not action.is_retry:
            return None
        max_attempts = int(self.configuration.get("max_attempts", 3))
        retryable = set(self.configuration.get("retryable_failure_classes", ["TRANSIENT_API_ERROR"]))

        check = ctx.side_effect_check
        if check is None or check.status == SideEffectStatus.UNKNOWN:
            return self._result(
                PolicyDecisionType.DENY,
                "Cannot confirm whether the original operation already took effect",
            )
        if check.status == SideEffectStatus.APPLIED:
            return self._result(
                PolicyDecisionType.DENY, "The operation already took effect; retrying would duplicate it"
            )
        if ctx.failure_class is not None and ctx.failure_class not in retryable:
            return self._result(
                PolicyDecisionType.DENY, f"Failure class {ctx.failure_class} is not safe to retry"
            )
        if action.attempt > max_attempts:
            return self._result(
                PolicyDecisionType.DENY, f"Retry budget exhausted after {max_attempts} attempts"
            )
        return self._result(
            PolicyDecisionType.ALLOW,
            f"Safe retry {action.attempt} of {max_attempts}: the operation did not take effect",
        )


class SensitiveAccountPolicy(PolicyRule):
    policy_type = "SENSITIVE_ACCOUNT"
    applies_to = frozenset({"update_merchant_bank_details", "update_merchant_contact"})

    def evaluate(self, action: ProposedAction, ctx: PolicyContext) -> PolicyRuleResult | None:
        if action.tool not in self.applies_to:
            return None
        return self._result(
            PolicyDecisionType.REQUIRES_APPROVAL,
            "Sensitive account changes require human verification",
        )


RULE_CLASSES: dict[str, type[PolicyRule]] = {
    cls.policy_type: cls
    for cls in (
        RefundLimitPolicy,
        SubjectiveDisputePolicy,
        TransactionStatePolicy,
        PendingPaymentPolicy,
        RetryPolicy,
        SensitiveAccountPolicy,
    )
}
