"""The four demo scenarios, plus proactive operation.

Each one names the exact transaction, the merchant's words and what should
happen, so a presenter can drive the whole demo from the dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .failure_injection import SimulationState


@dataclass
class Scenario:
    key: str
    title: str
    merchant_id: str
    transaction_id: str
    message: str
    expectation: str
    arm: Callable[[SimulationState, object], None] = lambda state, session: None


SCENARIOS: dict[str, Scenario] = {
    "A": Scenario(
        key="A",
        title="Settlement delay",
        merchant_id="M1001",
        transaction_id="TXN18293",
        message="My customer's payment failed, but the money was deducted.",
        expectation=(
            "Diagnoses a settlement delay rather than a failed payment, schedules a standby "
            "refund, tells the merchant the real position and waits. Resolves once settlement "
            "completes, or refunds if it fails."
        ),
    ),
    "B": Scenario(
        key="B",
        title="Refund API failure and recovery",
        merchant_id="M1001",
        transaction_id="TXN_REFUND_FAILURE",
        message="The customer cancelled order A-5521 and wants the ₹2,500 refunded.",
        expectation=(
            "The refund gateway fails on the first attempt. Saarthi checks whether the refund "
            "happened anyway, finds it did not, retries safely with the same idempotency key, "
            "verifies the result and resolves. Exactly one refund exists."
        ),
        arm=lambda state, session: setattr(state, "fail_next_refund_attempts", 1),
    ),
    "C": Scenario(
        key="C",
        title="High-value subjective dispute",
        merchant_id="M1001",
        transaction_id="TXN_HIGH_VALUE_DISPUTE",
        message="The customer says the product quality was poor and wants a ₹15,000 partial refund.",
        expectation=(
            "Recognises a subjective judgement above its authority, refuses to refund "
            "autonomously, and escalates with the transaction, dispute history, policy position "
            "and a recommendation ready for a person to act on."
        ),
    ),
    "D": Scenario(
        key="D",
        title="Nothing is wrong",
        merchant_id="M1001",
        transaction_id="TXN_NORMAL_SUCCESS",
        message="Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
        expectation=(
            "Confirms the payment succeeded and settled, tells the merchant, and takes no "
            "financial action at all."
        ),
    ),
}
