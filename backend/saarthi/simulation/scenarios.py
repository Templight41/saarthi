"""The Scenario Lab.

Five scenarios, each a complete statement of what Saarthi should do and — the
part that matters — where its authority stops. They are data, not code paths:
the agent has no idea which one is running, and nothing here can make it
behave differently. Arming a scenario only ever shapes the *world* (which
fixtures exist, whether the refund gateway will fail), never the agent.

Each scenario carries its own checkpoints, and a checkpoint is satisfied by an
audit event or a case status, never by parsing prose. That is what makes
`GET /api/scenarios/{id}/status` an observation rather than a claim: if the
run really did recover from a failed refund, there is a `RECOVERY_DECIDED`
row saying so, and if it did not, the checkpoint stays unticked.

`autonomy_boundary` is the field to read first. Three of the five end with
Saarthi doing the work; two end with it stopping, and stopping is the
behaviour being demonstrated.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import SettlementStatus
from ..database.models import Settlement
from .failure_injection import SimulationState


class ScenarioTrigger(StrEnum):
    MERCHANT_MESSAGE = "MERCHANT_MESSAGE"
    #: Nobody asks. The monitor notices. This is the differentiator.
    MONITOR = "MONITOR"


@dataclass(frozen=True)
class Checkpoint:
    """One observable thing that should happen, and how to tell that it did."""

    label: str
    event_type: str | None = None
    case_status: str | None = None


@dataclass(frozen=True)
class DemoStep:
    label: str
    detail: str
    #: The demo control a presenter would press here, if any.
    control: str | None = None


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    description: str
    capability: str
    merchant_id: str
    expected_outcome: str
    autonomy_boundary: str
    transaction_id: str | None = None
    #: What the merchant says. None for a proactive case: nobody says anything.
    message: str | None = None
    trigger: ScenarioTrigger = ScenarioTrigger.MERCHANT_MESSAGE
    aliases: tuple[str, ...] = ()
    initial_state: tuple[str, ...] = ()
    expected_behaviour: tuple[str, ...] = ()
    checkpoints: tuple[Checkpoint, ...] = ()
    demo_steps: tuple[DemoStep, ...] = ()
    arm: Callable[[SimulationState, AsyncSession], Awaitable[None]] = field(
        default=lambda state, session: _noop()
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "capability": self.capability,
            "merchant_id": self.merchant_id,
            "transaction_id": self.transaction_id,
            "message": self.message,
            "trigger": self.trigger.value,
            "aliases": list(self.aliases),
            "initial_state": list(self.initial_state),
            "expected_behaviour": list(self.expected_behaviour),
            "expected_outcome": self.expected_outcome,
            "autonomy_boundary": self.autonomy_boundary,
            "checkpoints": [
                {"label": c.label, "event_type": c.event_type, "case_status": c.case_status}
                for c in self.checkpoints
            ],
            "demo_steps": [
                {"label": s.label, "detail": s.detail, "control": s.control}
                for s in self.demo_steps
            ],
        }


async def _noop() -> None:
    return None


async def _arm_refund_failure(state: SimulationState, session: AsyncSession) -> None:
    state.fail_next_refund_attempts = 1


async def _arm_overdue_settlement(state: SimulationState, session: AsyncSession) -> None:
    """Make the settlement genuinely late, then let the monitor find it.

    Nothing is told that a demo is running: the settlement is simply overdue,
    and the ordinary monitor draws the ordinary conclusion.
    """
    settlement = await session.scalar(
        select(Settlement).where(Settlement.transaction_id == "TXN19931")
    )
    if settlement is None:
        return
    settlement.monitor_armed = True
    settlement.expected_at = utcnow() - timedelta(hours=2)
    settlement.status = SettlementStatus.PENDING


SCENARIOS: dict[str, Scenario] = {
    "settlement_delay": Scenario(
        id="settlement_delay",
        name="Settlement delay",
        aliases=("A",),
        capability="Autonomous resolution, and knowing when not to conclude yet",
        description=(
            "A customer paid, the money left their account, and the merchant's dashboard shows "
            "the payment as failed. The merchant believes they have lost a sale and their "
            "customer's money."
        ),
        merchant_id="M1001",
        transaction_id="TXN18293",
        message="My customer's payment failed, but the money was deducted.",
        initial_state=(
            "TXN18293 is PAYMENT_PENDING for ₹3,200, with the customer debited",
            "Settlement STL-2001 is PENDING and inside its expected window",
            "Three similar cases for this merchant are in memory",
        ),
        expected_behaviour=(
            "Reads the ledger rather than the merchant's account of it, and finds a pending "
            "payment rather than a failed one",
            "Overrides the model if it calls this a confirmed failure, and records the correction",
            "Schedules a standby refund so the customer is made whole if settlement never lands",
            "Tells the merchant the real position, then parks — it does not claim to have fixed "
            "anything it has not yet seen",
        ),
        expected_outcome=(
            "Parks awaiting settlement. Completing the settlement stands the standby refund down "
            "and resolves the case; failing it issues the refund instead."
        ),
        autonomy_boundary=(
            "Fully autonomous. Scheduling a conditional refund is within the merchant's ₹5,000 "
            "limit and reverses cleanly, so no person is needed either way."
        ),
        checkpoints=(
            Checkpoint("Transaction identified", event_type="TRANSACTION_IDENTIFIED"),
            Checkpoint("Similar cases retrieved", event_type="MEMORY_RETRIEVED"),
            Checkpoint("Diagnosed as a settlement delay", event_type="DIAGNOSIS_COMPLETE"),
            Checkpoint("Policy checked", event_type="POLICY_CHECKED"),
            Checkpoint("Standby refund scheduled", event_type="REFUND_SCHEDULED"),
            Checkpoint("Merchant notified", event_type="MESSAGE_SENT"),
            Checkpoint("Waiting on settlement", event_type="VERIFICATION_PENDING"),
            Checkpoint("Settlement verified", event_type="SETTLEMENT_VERIFIED"),
            Checkpoint("Resolved", case_status="RESOLVED"),
        ),
        demo_steps=(
            DemoStep("Run the scenario", "The merchant reports a failed payment."),
            DemoStep(
                "Watch it decline to agree",
                "The ledger says pending, not failed. The correction is recorded in "
                "clamped_fields rather than hidden.",
            ),
            DemoStep(
                "Complete the settlement",
                "Saarthi verifies it independently, stands the standby refund down and resolves.",
                control="POST /api/simulation/settlement/TXN18293/complete",
            ),
            DemoStep(
                "Or fail it instead",
                "The same case refunds the customer rather than resolving.",
                control="POST /api/simulation/settlement/TXN18293/fail",
            ),
        ),
    ),
    "refund_failure": Scenario(
        id="refund_failure",
        name="Refund failure and recovery",
        aliases=("B",),
        capability="Recovery without duplicate side effects",
        description=(
            "A straightforward refund, except the gateway fails on the first attempt. The "
            "interesting question is not whether Saarthi retries, but what it checks first."
        ),
        merchant_id="M1001",
        transaction_id="TXN_REFUND_FAILURE",
        message="The customer cancelled order A-5521 and wants the ₹2,500 refunded.",
        initial_state=(
            "TXN_REFUND_FAILURE is SUCCESS for ₹2,500 and settled",
            "The refund gateway is armed to fail exactly once",
            "No refund exists for this transaction",
        ),
        expected_behaviour=(
            "Issues the refund, and the gateway fails",
            "Classifies the failure rather than assuming it was transient",
            "Asks whether the side effect landed anyway, by looking the refund up on its "
            "idempotency key — it does not retry blind",
            "Retries with the same key, so a duplicate is impossible by construction",
            "Verifies by re-reading the refund, not by trusting the second call's response",
        ),
        expected_outcome=(
            "Resolved autonomously, with exactly one refund row and two action attempts against "
            "one idempotency key."
        ),
        autonomy_boundary=(
            "Fully autonomous. ₹2,500 is inside the merchant's ₹5,000 limit and the refund is "
            "an objective request, so no judgement is being delegated."
        ),
        checkpoints=(
            Checkpoint("Refund attempted", event_type="ACTION_STARTED"),
            Checkpoint("Attempt failed", event_type="ACTION_FAILED"),
            Checkpoint("Failure classified", event_type="FAILURE_CLASSIFIED"),
            Checkpoint("Checked whether the refund landed", event_type="REFUND_STATE_CHECKED"),
            Checkpoint("Recovery decided", event_type="RECOVERY_DECIDED"),
            Checkpoint("Retried on the same key", event_type="ACTION_RETRIED"),
            Checkpoint("Refund verified", event_type="ACTION_VERIFIED"),
            Checkpoint("Resolved", case_status="RESOLVED"),
        ),
        demo_steps=(
            DemoStep("Run the scenario", "The gateway is armed to fail once."),
            DemoStep(
                "Watch the failure not end the case",
                "ACTION_FAILED is followed by a lookup, not a retry.",
            ),
            DemoStep(
                "Check the refund count",
                "One refund row, two attempts, one idempotency key.",
                control="GET /api/cases/{case_id}/context",
            ),
        ),
        arm=_arm_refund_failure,
    ),
    "high_value_dispute": Scenario(
        id="high_value_dispute",
        name="High-value subjective dispute",
        aliases=("C",),
        capability="Knowing which decisions are not the agent's to make",
        description=(
            "A ₹15,000 refund demand over product quality, against a merchant authorised for "
            "₹5,000. Two independent reasons to stop, and Saarthi should name both."
        ),
        merchant_id="M1001",
        transaction_id="TXN_HIGH_VALUE_DISPUTE",
        message="The customer says the product quality was poor and wants a ₹15,000 partial refund.",
        initial_state=(
            "TXN_HIGH_VALUE_DISPUTE is SUCCESS for ₹18,000 and settled",
            "An open PRODUCT_QUALITY dispute requests ₹15,000",
            "The merchant's autonomous refund limit is ₹5,000",
        ),
        expected_behaviour=(
            "Recognises a subjective judgement, and does not resolve it by picking a side",
            "Refuses the refund on two grounds: the amount exceeds authority, and quality is "
            "not a matter of fact",
            "Escalates with the transaction, the dispute, the policy position, the checks it "
            "already completed and a recommendation",
            "On approval, runs the refund through the same executor and verifier as any "
            "autonomous action, recording the override in the audit trail",
        ),
        expected_outcome=(
            "Escalated to a person, with no refund issued. Approve, reject or take over — all "
            "three are demonstrable, and the approved path is verified like any other."
        ),
        autonomy_boundary=(
            "Stops here by design. This escalation carries human_required_by_policy, so it is "
            "excluded from the autonomy-rate denominator: counting it as a failure would punish "
            "exactly the behaviour we want."
        ),
        checkpoints=(
            Checkpoint("Dispute found", event_type="DISPUTE_CHECKED"),
            Checkpoint("Policy refused the refund", event_type="POLICY_CHECKED"),
            Checkpoint("Escalated with evidence", event_type="ESCALATION_CREATED"),
            Checkpoint("Awaiting a person", case_status="ESCALATED"),
            Checkpoint("Human approved", event_type="HUMAN_APPROVED"),
            Checkpoint("Refund verified after approval", event_type="ACTION_VERIFIED"),
        ),
        demo_steps=(
            DemoStep("Run the scenario", "A ₹15,000 quality dispute arrives."),
            DemoStep(
                "Read the escalation",
                "Why it stopped, what it already checked, and what it recommends.",
                control="GET /api/escalations?status=PENDING_HUMAN",
            ),
            DemoStep(
                "Approve, reject or take over",
                "Approval re-evaluates policy with the override, then executes and verifies.",
                control="POST /api/escalations/{id}/approve",
            ),
        ),
    ),
    "soundbox_mismatch": Scenario(
        id="soundbox_mismatch",
        name="Soundbox and payment mismatch",
        capability="Treating a merchant-facing signal as evidence, not as fact",
        description=(
            "The Soundbox on the counter announced a payment. The dashboard does not show it. "
            "A chatbot would agree with whichever one the merchant quoted; the announcement and "
            "the ledger are not the same kind of thing, and only one of them is authoritative."
        ),
        merchant_id="M1003",
        transaction_id=None,
        message="My Soundbox said payment received, but I don't see the payment in my dashboard.",
        initial_state=(
            "Three Soundbox announcements from device SB-77104412 in the last hour",
            "TXN_SOUNDBOX_OK — announced ₹240, ledger confirms SUCCESS and settled",
            "TXN_SOUNDBOX_PENDING — announced ₹180, ledger says PAYMENT_PENDING",
            "TXN20455 — announced ₹500, and the ledger has never heard of it",
        ),
        expected_behaviour=(
            "Reads the announcements through a tool, and reconciles each against the ledger "
            "rather than believing any of them",
            "Confirms the ₹240 payment is real and explains the dashboard discrepancy",
            "Reports the ₹180 payment as genuinely still pending, and monitors it",
            "For the ₹500 announcement, refuses to invent a transaction — there is nothing to "
            "refund, credit or apologise for yet",
        ),
        expected_outcome=(
            "Escalates the unmatched announcement rather than resolving it. A device reporting "
            "money that does not exist is a hardware, customer or fraud question, and all three "
            "are a person's call."
        ),
        autonomy_boundary=(
            "Stops at the phantom payment. Every autonomous option here begins by assuming the "
            "payment exists, and that assumption is the thing being tested."
        ),
        checkpoints=(
            Checkpoint("Announcements read", event_type="TRANSACTION_RETRIEVED"),
            Checkpoint("Diagnosed against the ledger", event_type="DIAGNOSIS_COMPLETE"),
            Checkpoint("Refused to invent a payment", event_type="CASE_ESCALATED"),
            Checkpoint("Awaiting a person", case_status="ESCALATED"),
        ),
        demo_steps=(
            DemoStep("Run the scenario", "The merchant reports what their Soundbox said."),
            DemoStep(
                "Read the reconciliation",
                "Each announcement, and what the ledger says about it. Confirmed, pending, and "
                "no such payment.",
                control="GET /api/cases/{case_id}/context",
            ),
            DemoStep(
                "Note what it did not do",
                "No refund, no credit, no confirmation of a payment nobody can find.",
            ),
        ),
    ),
    "proactive_anomaly": Scenario(
        id="proactive_anomaly",
        name="Proactive settlement anomaly",
        capability="Finding the problem before the merchant does",
        description=(
            "No merchant message. No ticket. A settlement is two hours past its window and the "
            "monitor notices, which is the difference between support and operations."
        ),
        merchant_id="M1001",
        transaction_id="TXN19931",
        message=None,
        trigger=ScenarioTrigger.MONITOR,
        initial_state=(
            "TXN19931 is PAYMENT_PENDING for ₹4,800 with the customer debited",
            "Settlement STL-2005 is two hours past its expected window",
            "The merchant has not been in touch and has no open case",
        ),
        expected_behaviour=(
            "The settlement monitor finds the overdue settlement on its own schedule",
            "A case is opened with origin PROACTIVE — nobody asked for it",
            "It runs the same pipeline as a merchant-raised case: same investigation, same "
            "policy engine, same verification. There is no second agent",
            "The merchant is told before they notice",
        ),
        expected_outcome=(
            "A case exists that no one reported, investigated and acted on through the ordinary "
            "loop."
        ),
        autonomy_boundary=(
            "Same boundary as any other case. Being proactive changes what starts the work, not "
            "what Saarthi is permitted to do once it has started."
        ),
        checkpoints=(
            Checkpoint("Monitor ran", event_type="WORKFLOW_TICK"),
            Checkpoint("Anomaly detected", event_type="PROACTIVE_ALERT_CREATED"),
            Checkpoint("Case opened unprompted", event_type="CASE_CREATED"),
            Checkpoint("Investigated", event_type="SETTLEMENT_CHECKED"),
            Checkpoint("Merchant notified", event_type="MESSAGE_SENT"),
        ),
        demo_steps=(
            DemoStep(
                "Run the scenario",
                "The settlement is made genuinely overdue, then the monitor runs.",
            ),
            DemoStep(
                "Note the origin",
                "The case reads PROACTIVE. There is no inbound merchant message on it at all.",
            ),
            DemoStep(
                "Follow the timeline",
                "It is the same pipeline a merchant-raised case runs.",
            ),
        ),
        arm=_arm_overdue_settlement,
    ),
}

#: Short keys presenters have in their fingers from earlier builds. Only the
#: three original scenarios have them: the two new ones are addressed by id, so
#: nobody can type a familiar letter and silently get a different scenario.
ALIASES: dict[str, str] = {
    alias.upper(): scenario.id for scenario in SCENARIOS.values() for alias in scenario.aliases
}


def resolve(name: str) -> Scenario | None:
    if name in SCENARIOS:
        return SCENARIOS[name]
    lowered = name.lower().replace("-", "_")
    if lowered in SCENARIOS:
        return SCENARIOS[lowered]
    alias = ALIASES.get(name.upper())
    return SCENARIOS.get(alias) if alias else None
