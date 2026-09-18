"""Recovery engine.

The rule that matters: never blindly retry a side effect. Before any retry the
manager asks the backend whether the operation already took effect, and if that
question cannot be answered it escalates rather than guessing. Retrying while
unsure is exactly how a customer gets refunded twice.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.enums import MessageStatus, RefundStatus
from ..policy.engine import PolicyEngine
from ..policy.rules import PolicyContext
from ..schemas.agent import (
    EscalationReason,
    FailureClass,
    PolicyDecisionType,
    ProposedAction,
    RecoveryDecision,
    RecoveryPlan,
    SideEffectCheck,
    SideEffectStatus,
)
from ..services import ops_service, refund_service
from ..services.errors import EnterpriseAPIError
from ..tools.executor import ActionFailure, PolicyViolation

RETRYABLE_CODES = {"UPSTREAM_TIMEOUT", "GATEWAY_UNAVAILABLE", "RATE_LIMITED"}
CONFLICT_CODES = {"INVALID_STATE", "AMOUNT_EXCEEDS_REFUNDABLE", "ALREADY_PROCESSED"}


class RecoveryManager:
    def __init__(self, settings: Settings, policy: PolicyEngine) -> None:
        self.settings = settings
        self.policy = policy
        self.max_attempts = settings.recovery_max_attempts

    # ------------------------------------------------------------------
    def classify(self, error: Exception) -> FailureClass:
        if isinstance(error, PolicyViolation):
            return FailureClass.POLICY_BLOCKED
        if isinstance(error, EnterpriseAPIError):
            if error.status >= 500 or error.code in RETRYABLE_CODES:
                return FailureClass.TRANSIENT_API_ERROR
            if error.status in {409, 422} or error.code in CONFLICT_CODES:
                return FailureClass.STATE_CONFLICT
            return FailureClass.UNKNOWN
        if isinstance(error, TimeoutError | ConnectionError):
            return FailureClass.TRANSIENT_API_ERROR
        return FailureClass.UNKNOWN

    async def check_side_effect(
        self, session: AsyncSession, step: ProposedAction, idempotency_key: str | None
    ) -> SideEffectCheck:
        """Did the operation land despite the error?"""
        try:
            if step.tool in {"issue_refund", "schedule_refund"}:
                if not idempotency_key:
                    return SideEffectCheck(
                        status=SideEffectStatus.UNKNOWN,
                        message="No idempotency key available to check against",
                    )
                refund = await refund_service.find_refund_by_idempotency_key(session, idempotency_key)
                if refund is None:
                    return SideEffectCheck(
                        status=SideEffectStatus.NOT_APPLIED,
                        evidence={"idempotency_key": idempotency_key},
                        message="No refund exists for this operation; the side effect did not occur",
                    )
                if refund.status in {RefundStatus.FAILED, RefundStatus.CANCELLED}:
                    return SideEffectCheck(
                        status=SideEffectStatus.NOT_APPLIED,
                        evidence={"refund_id": refund.id, "status": refund.status.value},
                        message=f"Refund {refund.id} exists but is {refund.status.value}",
                    )
                return SideEffectCheck(
                    status=SideEffectStatus.APPLIED,
                    evidence={"refund_id": refund.id, "status": refund.status.value},
                    message=f"Refund {refund.id} already exists with status {refund.status.value}",
                )

            if step.tool == "create_ticket":
                key = f"ticket:{step.args.get('case_id', '')}"
                ticket = await ops_service.find_ticket_by_idempotency_key(session, key)
                return SideEffectCheck(
                    status=SideEffectStatus.APPLIED if ticket else SideEffectStatus.NOT_APPLIED,
                    evidence={"ticket_id": ticket.id} if ticket else {},
                    message="Ticket already exists" if ticket else "No ticket was created",
                )

            if step.tool == "send_message":
                message_id = step.args.get("message_id")
                if not message_id:
                    return SideEffectCheck(status=SideEffectStatus.NOT_APPLIED)
                message = await ops_service.get_message(session, message_id)
                applied = message.status == MessageStatus.SENT
                return SideEffectCheck(
                    status=SideEffectStatus.APPLIED if applied else SideEffectStatus.NOT_APPLIED,
                    evidence={"message_id": message.id, "status": message.status.value},
                )
        except Exception as exc:  # noqa: BLE001
            return SideEffectCheck(
                status=SideEffectStatus.UNKNOWN,
                message=f"Could not determine whether the operation took effect: {exc}",
            )

        return SideEffectCheck(
            status=SideEffectStatus.NOT_APPLIED, message="Operation has no persistent side effect"
        )

    # ------------------------------------------------------------------
    async def recover(
        self,
        session: AsyncSession,
        failure: ActionFailure,
        *,
        policy_ctx: PolicyContext,
        planner=None,
        case_ctx=None,
    ) -> RecoveryPlan:
        failure_class = self.classify(failure.error)
        side_effect = await self.check_side_effect(
            session, failure.step, failure.action.idempotency_key
        )

        if side_effect.status == SideEffectStatus.APPLIED:
            return RecoveryPlan(
                decision=RecoveryDecision.SIDE_EFFECT_ALREADY_APPLIED,
                reason="The operation already took effect; verifying instead of retrying",
                failure_class=failure_class,
                side_effect=side_effect,
            )

        if side_effect.status == SideEffectStatus.UNKNOWN:
            return RecoveryPlan(
                decision=RecoveryDecision.ESCALATE,
                reason="Cannot confirm whether the operation took effect; retrying would be unsafe",
                failure_class=failure_class,
                side_effect=side_effect,
                escalation_reason=EscalationReason.SIDE_EFFECT_UNKNOWN.value,
            )

        if failure_class == FailureClass.TRANSIENT_API_ERROR:
            next_attempt = failure.action.attempt + 1
            retry_action = ProposedAction(
                tool=failure.step.tool,
                args=failure.step.args,
                purpose=failure.step.purpose,
                is_primary=failure.step.is_primary,
                is_retry=True,
                attempt=next_attempt,
            )
            policy_ctx.side_effect_check = side_effect
            policy_ctx.failure_class = failure_class.value
            decision = await self.policy.evaluate(session, retry_action, policy_ctx)
            if decision.decision == PolicyDecisionType.ALLOW:
                return RecoveryPlan(
                    decision=RecoveryDecision.RETRY,
                    reason=decision.reasons[0] if decision.reasons else "Safe retry permitted",
                    failure_class=failure_class,
                    side_effect=side_effect,
                    next_attempt=next_attempt,
                )
            return RecoveryPlan(
                decision=RecoveryDecision.ESCALATE,
                reason=decision.reasons[0] if decision.reasons else "Retry not permitted",
                failure_class=failure_class,
                side_effect=side_effect,
                escalation_reason=(
                    EscalationReason.RECOVERY_EXHAUSTED.value
                    if next_attempt > self.max_attempts
                    else EscalationReason.RETRY_DENIED.value
                ),
            )

        if failure_class == FailureClass.STATE_CONFLICT:
            alternative = None
            if planner is not None and case_ctx is not None:
                alternative = planner.alternative_for(failure.step, case_ctx)
            if alternative is not None:
                policy_ctx.side_effect_check = side_effect
                decision = await self.policy.evaluate(session, alternative, policy_ctx)
                if decision.decision == PolicyDecisionType.ALLOW:
                    return RecoveryPlan(
                        decision=RecoveryDecision.ALTERNATIVE,
                        reason="Original action conflicts with current state; using a safe alternative",
                        failure_class=failure_class,
                        side_effect=side_effect,
                        alternative=alternative,
                    )
            return RecoveryPlan(
                decision=RecoveryDecision.ESCALATE,
                reason="Action conflicts with current business state and has no safe alternative",
                failure_class=failure_class,
                side_effect=side_effect,
                escalation_reason=EscalationReason.STATE_CONFLICT.value,
            )

        return RecoveryPlan(
            decision=RecoveryDecision.ESCALATE,
            reason=f"Unrecognised failure ({failure_class.value}); escalating rather than guessing",
            failure_class=failure_class,
            side_effect=side_effect,
            escalation_reason=EscalationReason.RECOVERY_EXHAUSTED.value,
        )
