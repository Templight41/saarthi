"""The four policy cases the specification names, plus override semantics."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from saarthi.database.database import utcnow
from saarthi.database.enums import PaymentStatus
from saarthi.policy.engine import HUMAN_OVERRIDE_ID, PolicyEngine
from saarthi.policy.rules import PolicyContext
from saarthi.schemas.agent import (
    Diagnosis,
    HumanOverride,
    Intent,
    PolicyDecisionType,
    ProposedAction,
    RootCause,
    SideEffectCheck,
    SideEffectStatus,
)
from saarthi.services import ledger_service

pytestmark = pytest.mark.asyncio


async def _ctx(session, txn_id: str, **kwargs) -> PolicyContext:
    merchant = await ledger_service.get_merchant(session, "M1001")
    txn = await ledger_service.get_transaction(session, txn_id)
    try:
        settlement = await ledger_service.get_settlement(session, txn_id)
    except Exception:
        settlement = None
    disputes = await ledger_service.get_disputes(session, txn_id)
    return PolicyContext(
        merchant=merchant,
        now=utcnow(),
        transaction=txn,
        settlement=settlement,
        disputes=disputes,
        **kwargs,
    )


async def test_eligible_refund_is_allowed(seeded):
    """₹2,500 on a settled transaction, under the ₹5,000 limit → ALLOW."""
    engine = PolicyEngine()
    ctx = await _ctx(seeded, "TXN_REFUND_FAILURE")
    action = ProposedAction(
        tool="issue_refund", args={"amount": "2500.00", "transaction_id": "TXN_REFUND_FAILURE"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.ALLOW
    assert "POL-REFUND-LIMIT" in decision.policy_ids
    assert "POL-TXN-STATE" in decision.policy_ids


async def test_high_value_refund_requires_approval(seeded):
    """₹15,000 against a ₹5,000 limit → REQUIRES_APPROVAL."""
    engine = PolicyEngine()
    ctx = await _ctx(seeded, "TXN_HIGH_VALUE_DISPUTE")
    action = ProposedAction(
        tool="issue_refund", args={"amount": "15000.00", "transaction_id": "TXN_HIGH_VALUE_DISPUTE"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.REQUIRES_APPROVAL
    assert "POL-REFUND-LIMIT" in decision.policy_ids
    # Scenario C trips BOTH the limit and the subjectivity rule.
    assert "POL-SUBJECTIVE-DISPUTE" in decision.policy_ids


async def test_invalid_transaction_state_is_denied(seeded):
    """A failed, never-debited transaction → DENY, and DENY beats ALLOW."""
    txn = await ledger_service.get_transaction(seeded, "TXN_NORMAL_SUCCESS")
    txn.payment_status = PaymentStatus.FAILED
    txn.customer_debited = False
    await seeded.flush()

    engine = PolicyEngine()
    ctx = await _ctx(seeded, "TXN_NORMAL_SUCCESS")
    action = ProposedAction(
        tool="issue_refund", args={"amount": "1000.00", "transaction_id": "TXN_NORMAL_SUCCESS"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.DENY
    assert "POL-TXN-STATE" in decision.policy_ids


async def test_subjective_dispute_requires_approval_even_under_limit(seeded):
    """A product-quality dispute needs a human regardless of amount."""
    engine = PolicyEngine()
    ctx = await _ctx(seeded, "TXN_HIGH_VALUE_DISPUTE")
    action = ProposedAction(
        tool="issue_refund", args={"amount": "2000.00", "transaction_id": "TXN_HIGH_VALUE_DISPUTE"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.REQUIRES_APPROVAL
    assert "POL-SUBJECTIVE-DISPUTE" in decision.policy_ids
    assert "POL-REFUND-LIMIT" not in decision.policy_ids


async def test_human_override_allows_and_is_recorded(seeded):
    engine = PolicyEngine()
    override = HumanOverride(
        escalation_id="ESC-301", decided_by="ops@urbanthreads.in", decided_at=datetime.now(UTC)
    )
    ctx = await _ctx(seeded, "TXN_HIGH_VALUE_DISPUTE", human_override=override)
    action = ProposedAction(
        tool="issue_refund", args={"amount": "15000.00", "transaction_id": "TXN_HIGH_VALUE_DISPUTE"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.ALLOW
    assert decision.policy_ids[0] == HUMAN_OVERRIDE_ID
    assert "ESC-301" in decision.reasons[0]


async def test_human_override_cannot_beat_transaction_state(seeded):
    """Approval authorises a judgement call, not an impossible operation."""
    txn = await ledger_service.get_transaction(seeded, "TXN_NORMAL_SUCCESS")
    txn.payment_status = PaymentStatus.FAILED
    txn.customer_debited = False
    await seeded.flush()

    engine = PolicyEngine()
    override = HumanOverride(
        escalation_id="ESC-302", decided_by="ops@urbanthreads.in", decided_at=datetime.now(UTC)
    )
    ctx = await _ctx(seeded, "TXN_NORMAL_SUCCESS", human_override=override)
    action = ProposedAction(
        tool="issue_refund", args={"amount": "500.00", "transaction_id": "TXN_NORMAL_SUCCESS"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.DENY


async def test_pending_payment_denies_immediate_refund_but_allows_schedule(seeded):
    """Scenario A: investigate settlement before declaring failure."""
    engine = PolicyEngine()
    diagnosis = Diagnosis(
        intent=Intent.PAYMENT_DEBITED_BUT_NOT_CONFIRMED,
        root_cause=RootCause.SETTLEMENT_DELAY,
        confidence=0.94,
        risk="LOW",
    )
    ctx = await _ctx(seeded, "TXN18293", diagnosis=diagnosis)

    immediate = ProposedAction(
        tool="issue_refund", args={"amount": "3200.00", "transaction_id": "TXN18293"}
    )
    assert (await engine.evaluate(seeded, immediate, ctx)).decision == PolicyDecisionType.DENY

    scheduled = ProposedAction(
        tool="schedule_refund", args={"amount": "3200.00", "transaction_id": "TXN18293"}
    )
    decision = await engine.evaluate(seeded, scheduled, ctx)
    assert decision.decision == PolicyDecisionType.ALLOW


@pytest.mark.parametrize(
    "check_status,failure_class,attempt,expected",
    [
        (SideEffectStatus.NOT_APPLIED, "TRANSIENT_API_ERROR", 2, PolicyDecisionType.ALLOW),
        (SideEffectStatus.APPLIED, "TRANSIENT_API_ERROR", 2, PolicyDecisionType.DENY),
        (SideEffectStatus.UNKNOWN, "TRANSIENT_API_ERROR", 2, PolicyDecisionType.DENY),
        (SideEffectStatus.NOT_APPLIED, "STATE_CONFLICT", 2, PolicyDecisionType.DENY),
        (SideEffectStatus.NOT_APPLIED, "TRANSIENT_API_ERROR", 4, PolicyDecisionType.DENY),
    ],
)
async def test_retry_policy(seeded, check_status, failure_class, attempt, expected):
    engine = PolicyEngine()
    ctx = await _ctx(
        seeded,
        "TXN_REFUND_FAILURE",
        side_effect_check=SideEffectCheck(status=check_status),
        failure_class=failure_class,
    )
    action = ProposedAction(
        tool="issue_refund",
        args={"amount": "2500.00", "transaction_id": "TXN_REFUND_FAILURE"},
        is_retry=True,
        attempt=attempt,
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == expected


async def test_refund_over_remaining_amount_is_denied(seeded):
    txn = await ledger_service.get_transaction(seeded, "TXN_REFUND_FAILURE")
    txn.refunded_amount = Decimal("2000.00")
    await seeded.flush()

    engine = PolicyEngine()
    ctx = await _ctx(seeded, "TXN_REFUND_FAILURE")
    action = ProposedAction(
        tool="issue_refund", args={"amount": "2500.00", "transaction_id": "TXN_REFUND_FAILURE"}
    )
    decision = await engine.evaluate(seeded, action, ctx)
    assert decision.decision == PolicyDecisionType.DENY
