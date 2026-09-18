"""Escalation engine.

An escalation is a handover, not a shrug. It always carries what was already
checked, what the policy position is, what the agent recommends and the exact
action awaiting approval, so the human can decide in seconds rather than
starting the investigation again.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import EventType, list_events, record_event
from ..database.enums import EscalationStatus, EventStatus, RiskLevel
from ..database.ids import next_id
from ..database.models import Case, Escalation
from ..schemas.agent import (
    POLICY_MANDATED_REASONS,
    Diagnosis,
    EscalationReason,
    PolicyDecision,
    ProposedAction,
    Recommendation,
)

# Audit events that map to a human-readable "already checked" line.
CHECK_LABELS = {
    "TRANSACTION_RETRIEVED": "transaction verified",
    "SETTLEMENT_CHECKED": "settlement checked",
    "DISPUTE_CHECKED": "dispute history checked",
    "POLICY_CHECKED": "policy retrieved",
    "MEMORY_RETRIEVED": "similar cases reviewed",
    "MERCHANT_IDENTIFIED": "merchant identified",
    "ACTION_FAILED": "recovery attempted",
    "REFUND_STATE_CHECKED": "refund state confirmed",
}


class EscalationManager:
    def derive_reason(
        self, diagnosis: Diagnosis | None, decision: PolicyDecision | None
    ) -> EscalationReason:
        policy_ids = set(decision.policy_ids) if decision else set()
        limit_hit = "POL-REFUND-LIMIT" in policy_ids
        subjective = "POL-SUBJECTIVE-DISPUTE" in policy_ids

        if limit_hit and subjective:
            return EscalationReason.HIGH_VALUE_SUBJECTIVE_DISPUTE
        if subjective:
            return EscalationReason.SUBJECTIVE_DISPUTE
        if limit_hit:
            return EscalationReason.HIGH_VALUE_REFUND
        if "POL-SENSITIVE-ACCOUNT" in policy_ids:
            return EscalationReason.SENSITIVE_ACTION
        if diagnosis is not None and diagnosis.merchant_requests_human:
            return EscalationReason.MERCHANT_REQUESTED_HUMAN
        return EscalationReason.NO_PERMITTED_PLAN

    def recommend(self, reason: EscalationReason, diagnosis: Diagnosis | None) -> Recommendation:
        match reason:
            case EscalationReason.HIGH_VALUE_SUBJECTIVE_DISPUTE:
                return Recommendation.REVIEW_PARTIAL_REFUND
            case EscalationReason.HIGH_VALUE_REFUND:
                return Recommendation.APPROVE_REFUND
            case EscalationReason.SUBJECTIVE_DISPUTE:
                return Recommendation.REVIEW_PARTIAL_REFUND
            case (
                EscalationReason.RECOVERY_EXHAUSTED
                | EscalationReason.SIDE_EFFECT_UNKNOWN
                | EscalationReason.STATE_CONFLICT
                | EscalationReason.RETRY_DENIED
            ):
                return Recommendation.MANUAL_REFUND_REVIEW
            case EscalationReason.LOW_CONFIDENCE | EscalationReason.IDENTIFICATION_FAILED:
                return Recommendation.CLARIFY_WITH_MERCHANT
            case _:
                return Recommendation.MANUAL_INVESTIGATION

    async def completed_checks(self, session: AsyncSession, case: Case) -> list[str]:
        events = await list_events(session, case.id)
        seen: list[str] = []
        for event in events:
            label = CHECK_LABELS.get(event.event_type)
            if label and label not in seen:
                seen.append(label)
        return seen

    async def create(
        self,
        session: AsyncSession,
        case: Case,
        *,
        reason: EscalationReason,
        diagnosis: Diagnosis | None,
        decision: PolicyDecision | None,
        pending_action: ProposedAction | None,
        context_snapshot: dict,
        amount: Decimal | None = None,
    ) -> Escalation:
        recommendation = self.recommend(reason, diagnosis)
        checks = await self.completed_checks(session, case)

        escalation = Escalation(
            id=await next_id(session, "escalation"),
            case_id=case.id,
            reason=reason.value,
            risk=diagnosis.risk if diagnosis else RiskLevel.MEDIUM,
            amount=amount,
            recommendation=recommendation.value,
            status=EscalationStatus.PENDING_HUMAN,
            pending_action=pending_action.model_dump(mode="json") if pending_action else None,
            completed_actions=checks,
            context_snapshot=context_snapshot,
            policy=decision.model_dump(mode="json") if decision else None,
        )
        session.add(escalation)

        case.requires_human = True
        # Correct escalation must not be counted as an autonomy failure.
        case.human_required_by_policy = reason in POLICY_MANDATED_REASONS
        await session.flush()

        await record_event(
            session,
            case,
            EventType.ESCALATION_CREATED,
            message=f"Human approval required: {_humanise(reason)}",
            status=EventStatus.WARNING,
            result={
                "escalation_id": escalation.id,
                "reason": reason.value,
                "risk": escalation.risk.value if escalation.risk else None,
                "amount": str(amount) if amount is not None else None,
                "completed_actions": checks,
                "recommendation": recommendation.value,
                "status": escalation.status.value,
            },
        )
        return escalation

    async def pending_for_case(self, session: AsyncSession, case_id: str) -> Escalation | None:
        return await session.scalar(
            select(Escalation)
            .where(Escalation.case_id == case_id, Escalation.status == EscalationStatus.PENDING_HUMAN)
            .order_by(Escalation.created_at.desc())
        )

    async def latest_for_case(self, session: AsyncSession, case_id: str) -> Escalation | None:
        return await session.scalar(
            select(Escalation)
            .where(Escalation.case_id == case_id)
            .order_by(Escalation.created_at.desc())
        )


def _humanise(reason: EscalationReason) -> str:
    return reason.value.replace("_", " ").lower()
