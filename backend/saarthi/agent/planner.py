"""Goal-based planner.

A small decision table maps (intent, root cause, live facts) to a goal plus the
steps that would make that goal true. It is not one hardcoded workflow: a
single case can traverse several rows as the facts change. Scenario A alone
uses three of them, depending on whether settlement completes, fails, or is
still pending.

The model's diagnosis selects the row. Policy then gates every step. Nothing
here executes: it only proposes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from ..config import Settings
from ..database.database import utcnow
from ..schemas.agent import (
    Diagnosis,
    GoalCondition,
    Intent,
    Plan,
    PolicyDecision,
    PolicyDecisionType,
    ProposedAction,
    RootCause,
)
from .context import CaseContext

# Replan reasons raised by the verifier when live state changes under us.
REPLAN_SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
REPLAN_REFUND_CONDITION_MET = "REFUND_CONDITION_MET"


class Planner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    def candidate_plan(
        self, diagnosis: Diagnosis, ctx: CaseContext, *, replan_reason: str | None = None
    ) -> Plan:
        if replan_reason == REPLAN_SETTLEMENT_COMPLETED:
            return self._plan_settlement_completed(ctx)
        if replan_reason == REPLAN_REFUND_CONDITION_MET:
            return self._plan_refund_condition_met(diagnosis, ctx)

        match (diagnosis.intent, diagnosis.root_cause):
            case (_, RootCause.SETTLEMENT_DELAY):
                return self._plan_settlement_delay(diagnosis, ctx)
            case (Intent.PRODUCT_QUALITY_DISPUTE, _):
                return self._plan_quality_dispute(diagnosis, ctx)
            case (Intent.REFUND_REQUEST, _):
                return self._plan_refund_request(diagnosis, ctx)
            case (Intent.REFUND_STATUS, _):
                return self._plan_refund_status(ctx)
            case (Intent.NOTIFICATION_MISMATCH, _):
                return self._plan_notification_mismatch(ctx)
            case (_, RootCause.NO_ISSUE_FOUND):
                return self._plan_no_issue(ctx)
            case (_, RootCause.PAYMENT_FAILED_CONFIRMED):
                return self._plan_confirmed_failure(ctx)
            case _:
                return Plan(goal="No permitted action identified", steps=[])

    # ------------------------------------------------------------------
    def _txn(self, ctx: CaseContext) -> dict:
        return ctx.transaction or {}

    def _plan_settlement_delay(self, diagnosis: Diagnosis, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        settlement = ctx.settlement or {}
        amount = Decimal(str(txn.get("amount", "0")))

        expected_at = settlement.get("expected_at")
        deadline = utcnow() + timedelta(minutes=self.settings.refund_grace_minutes)
        if expected_at:
            from datetime import datetime

            deadline = datetime.fromisoformat(expected_at) + timedelta(
                minutes=self.settings.refund_grace_minutes
            )

        eta_text = _eta_text(ctx)
        return Plan(
            goal=(
                "The merchant understands the real position, and the customer is made whole "
                "automatically if settlement does not complete."
            ),
            steps=[
                ProposedAction(
                    tool="create_ticket",
                    args={
                        "subject": f"Settlement delay on {txn.get('id')}",
                        "description": diagnosis.summary,
                        "priority": "NORMAL",
                    },
                    purpose="Log the case for operations visibility",
                ),
                ProposedAction(
                    tool="schedule_refund",
                    args={
                        "transaction_id": txn.get("id"),
                        "amount": str(amount),
                        "reason": "Standby refund if settlement does not complete",
                        "deadline": deadline.isoformat(),
                        "condition_type": "SETTLEMENT_NOT_COMPLETED_BY",
                    },
                    purpose="Guarantee the customer is refunded if settlement fails",
                    is_primary=True,
                ),
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "INTERIM",
                        "facts": {
                            "transaction_id": txn.get("id"),
                            "amount": str(amount),
                            "eta_text": eta_text,
                        },
                    },
                    purpose="Explain the real position to the merchant",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[
                GoalCondition(
                    kind="SETTLEMENT_RESOLVED_OR_REFUNDED",
                    params={"transaction_id": txn.get("id"), "deadline": deadline.isoformat()},
                ),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
            primary_step=1,
        )

    def _plan_settlement_completed(self, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        scheduled = next((r for r in ctx.refunds if r["status"] == "SCHEDULED"), None)
        steps: list[ProposedAction] = []
        if scheduled is not None:
            steps.append(
                ProposedAction(
                    tool="cancel_scheduled_refund",
                    args={"refund_id": scheduled["id"], "reason": "settlement completed"},
                    purpose="Stand down the refund now that settlement landed",
                    is_primary=True,
                )
            )
        steps += [
            ProposedAction(
                tool="draft_message",
                args={
                    "stage": "SETTLED",
                    "facts": {"transaction_id": txn.get("id"), "amount": txn.get("amount")},
                },
                purpose="Confirm the outcome to the merchant",
            ),
            ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
        ]
        return Plan(
            goal="Settlement is confirmed complete and no refund is outstanding.",
            steps=steps,
            goal_conditions=[
                GoalCondition(kind="SETTLEMENT_COMPLETED", params={"transaction_id": txn.get("id")}),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
            primary_step=0 if scheduled else None,
            trigger=REPLAN_SETTLEMENT_COMPLETED,
        )

    def _plan_refund_condition_met(self, diagnosis: Diagnosis, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        amount = Decimal(str(txn.get("amount", "0")))
        return Plan(
            goal="The customer is refunded because settlement did not complete.",
            steps=[
                ProposedAction(
                    tool="issue_refund",
                    args={
                        "transaction_id": txn.get("id"),
                        "amount": str(amount),
                        "reason": "SETTLEMENT_FAILED",
                    },
                    purpose="Make the customer whole",
                    is_primary=True,
                ),
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "REFUNDED",
                        "facts": {"transaction_id": txn.get("id"), "amount": str(amount)},
                    },
                    purpose="Confirm the refund to the merchant",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[
                GoalCondition(kind="REFUND_COMPLETED", params={"transaction_id": txn.get("id")}),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
            primary_step=0,
            trigger=REPLAN_REFUND_CONDITION_MET,
        )

    def _plan_refund_request(self, diagnosis: Diagnosis, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        amount = diagnosis.requested_amount or Decimal(str(txn.get("amount", "0")))
        return Plan(
            goal="The customer's refund is issued and independently verified.",
            steps=[
                ProposedAction(
                    tool="issue_refund",
                    args={
                        "transaction_id": txn.get("id"),
                        "amount": str(amount),
                        "reason": "CUSTOMER_REFUND_REQUEST",
                    },
                    purpose="Refund the customer",
                    is_primary=True,
                ),
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "REFUNDED",
                        "facts": {"transaction_id": txn.get("id"), "amount": str(amount)},
                    },
                    purpose="Confirm the refund to the merchant",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[
                GoalCondition(kind="REFUND_COMPLETED", params={"transaction_id": txn.get("id")}),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
            primary_step=0,
        )

    def _plan_quality_dispute(self, diagnosis: Diagnosis, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        amount = diagnosis.requested_amount or Decimal(str(txn.get("amount", "0")))
        steps: list[ProposedAction] = []
        if not ctx.disputes:
            steps.append(
                ProposedAction(
                    tool="create_dispute",
                    args={
                        "transaction_id": txn.get("id"),
                        "type": "PRODUCT_QUALITY",
                        "description": diagnosis.summary,
                        "requested_amount": str(amount),
                    },
                    purpose="Record the dispute",
                )
            )
        steps.append(
            ProposedAction(
                tool="issue_refund",
                args={
                    "transaction_id": txn.get("id"),
                    "amount": str(amount),
                    "reason": "PRODUCT_QUALITY_DISPUTE",
                },
                purpose="Resolve the dispute in the customer's favour",
                is_primary=True,
            )
        )
        return Plan(
            goal="The quality dispute is decided by someone authorised to decide it.",
            steps=steps,
            goal_conditions=[
                GoalCondition(kind="REFUND_COMPLETED", params={"transaction_id": txn.get("id")})
            ],
            primary_step=len(steps) - 1,
        )

    def _plan_refund_status(self, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        latest = ctx.refunds[-1] if ctx.refunds else None
        return Plan(
            goal="The merchant knows the true refund state.",
            steps=[
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "OUTCOME",
                        "facts": {
                            "transaction_id": txn.get("id"),
                            "amount": latest["amount"] if latest else txn.get("amount"),
                            "refund_id": latest["id"] if latest else None,
                            "refund_status": latest["status"] if latest else "NONE",
                        },
                    },
                    purpose="Report the refund state",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[GoalCondition(kind="MESSAGE_SENT", params={})],
        )

    def _plan_no_issue(self, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        return Plan(
            goal="The merchant has an accurate answer and no needless action was taken.",
            steps=[
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "NO_ISSUE",
                        "facts": {"transaction_id": txn.get("id"), "amount": txn.get("amount")},
                    },
                    purpose="Confirm that nothing is wrong",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[
                GoalCondition(
                    kind="TRANSACTION_STATE_MATCHES",
                    params={
                        "transaction_id": txn.get("id"),
                        "payment_status": "SUCCESS",
                    },
                ),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
        )

    def _plan_notification_mismatch(self, ctx: CaseContext) -> Plan:
        """The device announced a payment and the ledger confirms it.

        Only reachable once reconciliation has matched the announcement to a
        successful transaction — a pending one is clamped to a settlement
        delay, and an unmatched one never reaches a plan at all, because the
        case escalates before planning. So this says what is true and stops:
        the discrepancy was the merchant's dashboard, not their money.
        """
        txn = self._txn(ctx)
        confirmed = [n for n in ctx.notifications if n.get("confirmed_by_ledger")]
        announced = confirmed[0] if confirmed else {}
        return Plan(
            goal="The merchant knows the payment is real and where to see it.",
            steps=[
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "NO_ISSUE",
                        "facts": {
                            "transaction_id": txn.get("id"),
                            "amount": txn.get("amount"),
                            "announced_at": announced.get("announced_at"),
                            "confirmed_by_ledger": True,
                        },
                    },
                    purpose="Confirm the announced payment against the ledger",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[
                GoalCondition(
                    kind="TRANSACTION_STATE_MATCHES",
                    params={"transaction_id": txn.get("id"), "payment_status": "SUCCESS"},
                ),
                GoalCondition(kind="MESSAGE_SENT", params={}),
            ],
        )

    def _plan_confirmed_failure(self, ctx: CaseContext) -> Plan:
        txn = self._txn(ctx)
        return Plan(
            goal="The merchant knows the payment genuinely failed and no money is owed.",
            steps=[
                ProposedAction(
                    tool="draft_message",
                    args={
                        "stage": "OUTCOME",
                        "facts": {"transaction_id": txn.get("id"), "amount": txn.get("amount")},
                    },
                    purpose="Explain the confirmed failure",
                ),
                ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
            ],
            goal_conditions=[GoalCondition(kind="MESSAGE_SENT", params={})],
        )

    # ------------------------------------------------------------------
    def finalize(self, plan: Plan, decisions: dict[int, PolicyDecision], ctx: CaseContext) -> Plan:
        """Drop denied steps, and substitute a permitted fallback where one exists."""
        steps: list[ProposedAction] = []
        primary_index: int | None = None

        for index, step in enumerate(plan.steps):
            decision = decisions.get(index)
            if decision is None or decision.decision == PolicyDecisionType.ALLOW:
                if step.is_primary:
                    primary_index = len(steps)
                steps.append(step)
                continue

            if decision.decision == PolicyDecisionType.DENY:
                fallback = self._fallback_for(step, decision, ctx)
                if fallback is not None:
                    if step.is_primary:
                        primary_index = len(steps)
                    steps.append(fallback)
                continue
            # REQUIRES_APPROVAL never reaches here: the supervisor escalates first.

        return Plan(
            goal=plan.goal,
            steps=steps,
            goal_conditions=plan.goal_conditions,
            primary_step=primary_index,
            trigger=plan.trigger,
        )

    def _fallback_for(
        self, step: ProposedAction, decision: PolicyDecision, ctx: CaseContext
    ) -> ProposedAction | None:
        """An immediate refund blocked by a pending settlement becomes a standby refund."""
        if step.tool != "issue_refund":
            return None
        if "POL-PENDING-PAYMENT" not in decision.policy_ids:
            return None

        settlement = ctx.settlement or {}
        expected_at = settlement.get("expected_at")
        deadline = utcnow() + timedelta(minutes=self.settings.refund_grace_minutes)
        if expected_at:
            from datetime import datetime

            deadline = datetime.fromisoformat(expected_at) + timedelta(
                minutes=self.settings.refund_grace_minutes
            )
        return ProposedAction(
            tool="schedule_refund",
            args={
                "transaction_id": step.args.get("transaction_id"),
                "amount": step.args.get("amount"),
                "reason": "Settlement still pending; standby refund scheduled instead",
                "deadline": deadline.isoformat(),
                "condition_type": "SETTLEMENT_NOT_COMPLETED_BY",
            },
            purpose="Permitted alternative to an immediate refund",
            is_primary=step.is_primary,
        )

    def alternative_for(self, step: ProposedAction, ctx: CaseContext) -> ProposedAction | None:
        """Used by recovery after a state conflict."""
        if step.tool == "issue_refund":
            txn = self._txn(ctx)
            return ProposedAction(
                tool="create_ticket",
                args={
                    "subject": f"Manual refund review for {txn.get('id')}",
                    "description": "Automated refund hit a state conflict and needs a human check.",
                    "priority": "HIGH",
                },
                purpose="Escalate to operations without taking an unsafe action",
            )
        return None


def _eta_text(ctx: CaseContext) -> str:
    eta = (ctx.settlement_eta or {}).get("eta_seconds")
    if eta is None:
        return "shortly"
    if eta < 0:
        return "already overdue"
    hours, remainder = divmod(int(eta), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return f"in about {hours}h {minutes}m"
    if hours:
        return f"in about {hours}h"
    return f"in about {minutes}m"
