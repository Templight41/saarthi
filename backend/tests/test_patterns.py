"""Recurring pattern intelligence.

The point of these tests is that the numbers are *counted*. Most of them build
their own rows and then assert on an exact count, a threshold boundary or a
window edge — because a pattern engine that is roughly right is worse than none
at all, when the output is a sentence like "this is your third delay this
month".

The last group is the one that matters most: a pattern is advisory. It reaches
the model and the dashboard, and it never reaches the policy engine.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from saarthi.database.database import utcnow
from saarthi.database.enums import (
    CaseStatus,
    EscalationStatus,
    PaymentStatus,
    ReconciliationOutcome,
    RefundStatus,
    RiskLevel,
    SettlementStatus,
)
from saarthi.database.models import (
    Case,
    Escalation,
    Merchant,
    Refund,
    Settlement,
    Transaction,
)
from saarthi.memory.patterns import (
    RULES,
    PatternSeverity,
    PatternType,
    detect_patterns,
    merchant_profile,
)
from saarthi.services import notification_service

pytestmark = pytest.mark.asyncio


async def _merchant(session, merchant_id: str) -> Merchant:
    merchant = Merchant(
        id=merchant_id,
        name=f"Merchant {merchant_id}",
        risk_level=RiskLevel.LOW,
        autonomous_refund_limit=Decimal("5000.00"),
    )
    session.add(merchant)
    await session.flush()
    return merchant


async def _late_settlement(session, merchant_id: str, suffix: str, *, days_ago: float):
    """A transaction whose settlement landed well outside its window."""
    now = utcnow()
    expected = now - timedelta(days=days_ago)
    txn = Transaction(
        id=f"TXN{suffix}",
        merchant_id=merchant_id,
        amount=Decimal("1000.00"),
        payment_status=PaymentStatus.SUCCESS,
        customer_debited=True,
    )
    session.add(txn)
    await session.flush()
    session.add(
        Settlement(
            id=f"STL{suffix}",
            transaction_id=txn.id,
            status=SettlementStatus.COMPLETED,
            expected_at=expected,
            completed_at=expected + timedelta(hours=3),
        )
    )
    await session.flush()
    return txn


def _pattern(patterns, kind: PatternType):
    return next((p for p in patterns if p.pattern_type == kind), None)


# --------------------------------------------------------------------------
# Counting, thresholds and windows
# --------------------------------------------------------------------------
async def test_a_pattern_needs_to_meet_its_threshold(session):
    await _merchant(session, "M9001")
    rule = RULES[PatternType.SETTLEMENT_DELAY]

    for i in range(rule.threshold - 1):
        await _late_settlement(session, "M9001", f"90{i}", days_ago=i + 1)
    assert _pattern(await detect_patterns(session, "M9001"), PatternType.SETTLEMENT_DELAY) is None

    await _late_settlement(session, "M9001", "909", days_ago=5)
    found = _pattern(await detect_patterns(session, "M9001"), PatternType.SETTLEMENT_DELAY)
    assert found is not None
    assert found.event_count == rule.threshold
    assert found.severity == PatternSeverity.MEDIUM


async def test_twice_the_threshold_is_more_serious(session):
    await _merchant(session, "M9002")
    for i in range(RULES[PatternType.SETTLEMENT_DELAY].threshold * 2):
        await _late_settlement(session, "M9002", f"91{i}", days_ago=i + 1)

    found = _pattern(await detect_patterns(session, "M9002"), PatternType.SETTLEMENT_DELAY)
    assert found.severity == PatternSeverity.HIGH
    assert found.confidence == 1.0


async def test_occurrences_outside_the_window_do_not_count(session):
    await _merchant(session, "M9003")
    for i in range(3):
        await _late_settlement(session, "M9003", f"92{i}", days_ago=i + 1)
    # Well past the thirty-day window.
    await _late_settlement(session, "M9003", "929", days_ago=45)

    found = _pattern(await detect_patterns(session, "M9003"), PatternType.SETTLEMENT_DELAY)
    assert found.event_count == 3
    assert "TXN929" not in found.related_transactions


async def test_a_settlement_inside_its_grace_period_is_not_a_delay(session):
    """A minute late is noise. Counting it would make the number meaningless."""
    await _merchant(session, "M9004")
    now = utcnow()
    for i in range(4):
        txn = Transaction(
            id=f"TXN93{i}",
            merchant_id="M9004",
            amount=Decimal("100.00"),
            payment_status=PaymentStatus.SUCCESS,
        )
        session.add(txn)
        await session.flush()
        expected = now - timedelta(days=i + 1)
        session.add(
            Settlement(
                id=f"STL93{i}",
                transaction_id=txn.id,
                status=SettlementStatus.COMPLETED,
                expected_at=expected,
                completed_at=expected + timedelta(minutes=2),
            )
        )
    await session.flush()

    assert _pattern(await detect_patterns(session, "M9004"), PatternType.SETTLEMENT_DELAY) is None


async def test_one_merchants_problems_are_not_anothers(session):
    await _merchant(session, "M9005")
    await _merchant(session, "M9006")
    for i in range(4):
        await _late_settlement(session, "M9005", f"94{i}", days_ago=i + 1)

    assert _pattern(await detect_patterns(session, "M9005"), PatternType.SETTLEMENT_DELAY)
    assert await detect_patterns(session, "M9006") == []


async def test_a_burst_is_measured_by_density_not_by_total(session):
    """Five failures across a month is a business. Five in ten minutes is a
    broken terminal, and only the second is worth telling anyone about."""
    await _merchant(session, "M9007")
    now = utcnow()
    spread = [timedelta(days=d) for d in (1, 5, 9, 14, 20)]
    tight = [timedelta(days=3, minutes=m) for m in (0, 1, 2, 4, 6)]

    for i, delta in enumerate(spread):
        session.add(
            Transaction(
                id=f"TXNSPREAD{i}",
                merchant_id="M9007",
                amount=Decimal("100.00"),
                payment_status=PaymentStatus.FAILED,
                created_at=now - delta,
            )
        )
    await session.flush()
    assert _pattern(await detect_patterns(session, "M9007"), PatternType.PAYMENT_FAILURE_BURST) is None

    for i, delta in enumerate(tight):
        session.add(
            Transaction(
                id=f"TXNTIGHT{i}",
                merchant_id="M9007",
                amount=Decimal("100.00"),
                payment_status=PaymentStatus.FAILED,
                created_at=now - delta,
            )
        )
    await session.flush()

    found = _pattern(await detect_patterns(session, "M9007"), PatternType.PAYMENT_FAILURE_BURST)
    assert found.event_count == 5
    assert all(t.startswith("TXNTIGHT") for t in found.related_transactions)


async def test_a_refund_that_needed_a_retry_counts_as_a_failure(session):
    await _merchant(session, "M9008")
    now = utcnow()
    for i, (status, attempts) in enumerate(
        [(RefundStatus.FAILED, 1), (RefundStatus.COMPLETED, 2), (RefundStatus.COMPLETED, 1)]
    ):
        txn = Transaction(
            id=f"TXN95{i}",
            merchant_id="M9008",
            amount=Decimal("500.00"),
            payment_status=PaymentStatus.SUCCESS,
        )
        session.add(txn)
        await session.flush()
        session.add(
            Refund(
                id=f"RFD95{i}",
                transaction_id=txn.id,
                amount=Decimal("500.00"),
                status=status,
                attempt_count=attempts,
                idempotency_key=f"k95{i}",
                created_at=now - timedelta(days=i + 1),
            )
        )
    await session.flush()

    found = _pattern(await detect_patterns(session, "M9008"), PatternType.REFUND_FAILURE)
    # The clean first-attempt refund is not a failure.
    assert found.event_count == 2


async def test_escalations_are_grouped_by_reason(session):
    """The same judgement reaching a person repeatedly is the signal; a mixed
    bag of one-offs is not."""
    await _merchant(session, "M9009")
    now = utcnow()
    for i, reason in enumerate(["REFUND_LIMIT", "REFUND_LIMIT", "AMBIGUOUS_TRANSACTION"]):
        case = Case(
            id=f"CASE96{i}",
            merchant_id="M9009",
            status=CaseStatus.ESCALATED,
            original_message="x",
            created_at=now - timedelta(days=i + 1),
        )
        session.add(case)
        await session.flush()
        session.add(
            Escalation(
                id=f"ESC96{i}",
                case_id=case.id,
                reason=reason,
                status=EscalationStatus.PENDING_HUMAN,
                created_at=now - timedelta(days=i + 1),
            )
        )
    await session.flush()

    found = _pattern(await detect_patterns(session, "M9009"), PatternType.REPEATED_ESCALATION)
    assert found.event_count == 2
    assert "refund limit" in found.summary


async def test_a_volume_spike_is_measured_against_the_merchants_own_baseline(session):
    await _merchant(session, "M9010")
    now = utcnow()
    # A steady four cases a week is this merchant's normal, not an anomaly.
    for week in range(1, 5):
        for n in range(4):
            session.add(
                Case(
                    id=f"CASE97{week}{n}",
                    merchant_id="M9010",
                    status=CaseStatus.RESOLVED,
                    original_message="x",
                    created_at=now - timedelta(days=week * 7 + n),
                )
            )
    await session.flush()
    for n in range(4):
        session.add(
            Case(
                id=f"CASE980{n}",
                merchant_id="M9010",
                status=CaseStatus.RESOLVED,
                original_message="x",
                created_at=now - timedelta(days=n),
            )
        )
    await session.flush()
    assert _pattern(await detect_patterns(session, "M9010"), PatternType.CASE_VOLUME_SPIKE) is None

    for n in range(8):
        session.add(
            Case(
                id=f"CASE990{n}",
                merchant_id="M9010",
                status=CaseStatus.RESOLVED,
                original_message="x",
                created_at=now - timedelta(days=1, hours=n),
            )
        )
    await session.flush()

    found = _pattern(await detect_patterns(session, "M9010"), PatternType.CASE_VOLUME_SPIKE)
    assert found is not None
    assert found.event_count == 12


async def test_an_announced_payment_the_ledger_cannot_confirm_is_a_mismatch(session):
    """The Soundbox announces; the ledger decides.

    Four announcements, one per authoritative outcome. Only the one the ledger
    confirms is not a mismatch — including the announcement for a transaction
    that does not exist at all, which is the case that matters most.
    """
    await _merchant(session, "M9011")
    assert _pattern(await detect_patterns(session, "M9011"), PatternType.NOTIFICATION_MISMATCH) is None

    now = utcnow()
    statuses = [PaymentStatus.FAILED, PaymentStatus.PAYMENT_PENDING, PaymentStatus.SUCCESS]
    for i, status in enumerate(statuses):
        session.add(
            Transaction(
                id=f"TXN99{i}", merchant_id="M9011", amount=Decimal("100.00"), payment_status=status
            )
        )
        await session.flush()
        await notification_service.record_announcement(
            session,
            merchant_id="M9011",
            reference=f"TXN99{i}",
            announced_amount=Decimal("100.00"),
            announced_at=now - timedelta(days=i + 1),
        )
    # A payment the ledger has never heard of.
    await notification_service.record_announcement(
        session,
        merchant_id="M9011",
        reference="TXN-PHANTOM",
        announced_amount=Decimal("100.00"),
        announced_at=now - timedelta(days=1),
    )
    await session.flush()

    found = _pattern(await detect_patterns(session, "M9011"), PatternType.NOTIFICATION_MISMATCH)
    assert found is not None
    # Failed, pending and phantom. The successful one is not a mismatch.
    assert found.event_count == 3


async def test_an_announcement_is_scoped_to_the_merchant_who_heard_it(session):
    """A device must not surface another merchant's transaction by naming it."""
    await _merchant(session, "M9012")
    await _merchant(session, "M9013")
    session.add(
        Transaction(
            id="TXN-OTHER",
            merchant_id="M9013",
            amount=Decimal("100.00"),
            payment_status=PaymentStatus.SUCCESS,
        )
    )
    await session.flush()

    event = await notification_service.record_announcement(
        session, merchant_id="M9012", reference="TXN-OTHER", announced_amount=Decimal("100.00")
    )
    result = await notification_service.reconcile(session, event)

    assert result.outcome is ReconciliationOutcome.NO_AUTHORITATIVE_RECORD
    assert result.transaction_id is None


# --------------------------------------------------------------------------
# The profile
# --------------------------------------------------------------------------
async def test_a_merchant_with_no_history_has_an_empty_profile(session):
    await _merchant(session, "M9012")
    profile = await merchant_profile(session, "M9012")

    assert profile["total_cases"] == 0
    assert profile["patterns"] == []
    # No division by a zero case count.
    assert profile["median_resolution_seconds"] is None


async def test_the_profile_counts_what_actually_happened(seeded):
    profile = await merchant_profile(seeded, "M1001")

    assert profile["total_cases"] >= 4
    assert profile["autonomous_resolutions"] >= 1
    assert profile["median_resolution_seconds"] > 0
    assert any("settlement delays" in h for h in profile["headlines"])
    # Never presentable as current payment state.
    assert profile["authoritative"] is False
    assert profile["source"] == "counted_from_postgres"


async def test_the_seeded_merchant_has_the_patterns_the_demo_describes(seeded):
    urban = await detect_patterns(seeded, "M1001")
    kaveri = await detect_patterns(seeded, "M1002")

    delays = _pattern(urban, PatternType.SETTLEMENT_DELAY)
    assert delays is not None and delays.event_count >= 3
    # The forty-day-old case is real history but outside the window.
    assert "TXN16210" not in delays.related_transactions

    assert _pattern(kaveri, PatternType.PAYMENT_FAILURE_BURST) is not None
    assert _pattern(kaveri, PatternType.REFUND_FAILURE) is not None
    # Urban Threads' refunds are fine; Kaveri's settlements are.
    assert _pattern(urban, PatternType.REFUND_FAILURE) is None
    assert _pattern(kaveri, PatternType.SETTLEMENT_DELAY) is None


# --------------------------------------------------------------------------
# Advisory, not authoritative
# --------------------------------------------------------------------------
async def test_patterns_are_labelled_advisory_wherever_they_travel(seeded):
    from saarthi.agent.context import build_context

    case = await seeded.get(Case, "CASE-17421")
    ctx = await build_context(seeded, case)

    assert ctx.patterns, "the seeded merchant has patterns"
    assert all(p["authoritative"] is False for p in ctx.patterns)
    # The key the model sees says what it is.
    prompt = ctx.for_prompt()
    assert "merchant_patterns_advisory_only" in prompt
    assert prompt["merchant_patterns_advisory_only"] == ctx.patterns


async def test_the_policy_engine_cannot_see_patterns():
    """Structural: how often this happened before must not change what Saarthi
    is allowed to do about it today."""
    import pathlib

    policy_dir = pathlib.Path(__file__).resolve().parents[1] / "saarthi" / "policy"
    offenders = [
        path.name for path in policy_dir.rglob("*.py") if "pattern" in path.read_text().lower()
    ]
    assert offenders == []


async def test_a_history_of_delays_does_not_change_the_refund_decision(seeded):
    """Same request, same policy answer, whether or not the merchant has form."""
    from saarthi.policy.engine import PolicyEngine
    from saarthi.policy.rules import PolicyContext
    from saarthi.schemas.agent import ProposedAction

    merchant = await seeded.get(Merchant, "M1001")
    txn = await seeded.get(Transaction, "TXN18293")
    engine = PolicyEngine()
    action = ProposedAction(
        tool="issue_refund", args={"transaction_id": txn.id, "amount": Decimal("3200.00")}
    )

    patterns = await detect_patterns(seeded, "M1001")
    assert patterns, "M1001 has a settlement-delay history"

    ctx = PolicyContext(merchant=merchant, now=utcnow(), transaction=txn)
    before = await engine.evaluate(seeded, action, ctx)

    for _ in range(6):
        await _late_settlement(seeded, "M1001", f"98{_}", days_ago=_ + 1)
    worse = await detect_patterns(seeded, "M1001")
    assert worse[0].severity == PatternSeverity.HIGH, "the history got materially worse"

    after = await engine.evaluate(
        seeded, action, PolicyContext(merchant=merchant, now=utcnow(), transaction=txn)
    )
    assert (before.decision, before.policy_ids) == (after.decision, after.policy_ids)


async def test_current_state_still_wins_over_a_history_of_delays(seeded):
    """The merchant has three settlement delays on file. This payment settled
    successfully, and that is what the diagnosis has to say."""
    from saarthi.agent.context import build_context
    from saarthi.agent.diagnosis import clamp_to_facts
    from saarthi.schemas.agent import Diagnosis, Intent, RootCause

    case = Case(
        id="CASE-PATTERN-1",
        merchant_id="M1001",
        transaction_id="TXN_NORMAL_SUCCESS",
        status=CaseStatus.INVESTIGATING,
        original_message="Did this one go through?",
    )
    seeded.add(case)
    await seeded.flush()

    ctx = await build_context(seeded, case)
    assert ctx.patterns, "the merchant does have a delay history"
    assert ctx.transaction["payment_status"] == "SUCCESS"

    # A model that let the history colour its answer.
    diagnosis = clamp_to_facts(
        Diagnosis(
            intent=Intent.SETTLEMENT_DELAY,
            root_cause=RootCause.SETTLEMENT_DELAY,
            confidence=0.9,
            risk=RiskLevel.LOW,
            summary="Another settlement delay for this merchant.",
            transaction_id="TXN_NORMAL_SUCCESS",
        ),
        ctx,
    )

    # Clamping reads the ledger, not the pattern list, so nothing here is
    # rewritten *because of* history — and nothing is excused by it either.
    assert ctx.transaction["payment_status"] == "SUCCESS"
    assert ctx.settlement["status"] == "COMPLETED"
    assert diagnosis.transaction_id == "TXN_NORMAL_SUCCESS"
