"""Refund idempotency and deterministic failure injection.

These are the guarantees the recovery demo rests on: a failure leaves no row
behind, and a replay never issues a second refund.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from saarthi.database.enums import PaymentStatus, RefundStatus
from saarthi.services import ledger_service, refund_service
from saarthi.services.errors import EnterpriseAPIError
from saarthi.simulation.failure_injection import simulation_state

pytestmark = pytest.mark.asyncio

KEY = "refund:CASE-18293:TXN_REFUND_FAILURE:2500.00"


async def _issue(session, amount="2500.00", key=KEY, txn="TXN_REFUND_FAILURE"):
    return await refund_service.issue_refund(
        session,
        transaction_id=txn,
        amount=Decimal(amount),
        reason="customer cancelled",
        idempotency_key=key,
        case_id=None,
        simulation=simulation_state,
    )


async def test_refund_succeeds_and_updates_transaction(seeded):
    refund = await _issue(seeded)
    assert refund.status == RefundStatus.COMPLETED
    assert refund.completed_at is not None

    txn = await ledger_service.get_transaction(seeded, "TXN_REFUND_FAILURE")
    assert txn.payment_status == PaymentStatus.REFUNDED
    assert Decimal(txn.refunded_amount) == Decimal("2500.00")


async def test_first_attempt_fails_without_writing_a_row(seeded):
    """The failure must happen BEFORE any side effect, or recovery is unsafe."""
    simulation_state.fail_next_refund_attempts = 1

    with pytest.raises(EnterpriseAPIError) as exc:
        await _issue(seeded)
    assert exc.value.status == 500
    assert exc.value.retryable is True

    # Verification must genuinely find nothing.
    assert await refund_service.find_refund_by_idempotency_key(seeded, KEY) is None

    txn = await ledger_service.get_transaction(seeded, "TXN_REFUND_FAILURE")
    assert Decimal(txn.refunded_amount) == Decimal("0.00")


async def test_second_attempt_succeeds_deterministically(seeded):
    simulation_state.fail_next_refund_attempts = 1

    with pytest.raises(EnterpriseAPIError):
        await _issue(seeded)
    refund = await _issue(seeded)

    assert refund.status == RefundStatus.COMPLETED
    assert refund.attempt_count == 2

    refunds = await refund_service.list_refunds_for_transaction(seeded, "TXN_REFUND_FAILURE")
    assert len(refunds) == 1, "a retry must not create a second refund"


async def test_replay_with_same_key_returns_same_refund(seeded):
    first = await _issue(seeded)
    second = await _issue(seeded)
    assert first.id == second.id

    txn = await ledger_service.get_transaction(seeded, "TXN_REFUND_FAILURE")
    assert Decimal(txn.refunded_amount) == Decimal("2500.00"), "must not double-apply"


async def test_refund_on_pending_settlement_is_rejected(seeded):
    with pytest.raises(EnterpriseAPIError) as exc:
        await _issue(seeded, amount="3200.00", key="k-a", txn="TXN18293")
    assert exc.value.status == 409
    assert exc.value.retryable is False


async def test_refund_allowed_once_settlement_failed(seeded):
    await ledger_service.fail_settlement(seeded, "TXN18293", "BANK_REJECTED")
    refund = await _issue(seeded, amount="3200.00", key="k-b", txn="TXN18293")
    assert refund.status == RefundStatus.COMPLETED


async def test_amount_over_remaining_is_rejected(seeded):
    with pytest.raises(EnterpriseAPIError) as exc:
        await _issue(seeded, amount="9999.00", key="k-c")
    assert exc.value.code == "AMOUNT_EXCEEDS_REFUNDABLE"


async def test_scheduled_refund_can_be_cancelled(seeded):
    from datetime import timedelta

    from saarthi.database.database import utcnow

    refund = await refund_service.schedule_refund(
        seeded,
        transaction_id="TXN18293",
        amount=Decimal("3200.00"),
        reason="settlement standby",
        condition={"type": "SETTLEMENT_NOT_COMPLETED_BY"},
        deadline=utcnow() + timedelta(hours=1),
        idempotency_key="sched-1",
        case_id=None,
    )
    assert refund.status == RefundStatus.SCHEDULED

    cancelled = await refund_service.cancel_scheduled_refund(seeded, refund.id, "settlement completed")
    assert cancelled.status == RefundStatus.CANCELLED

    with pytest.raises(EnterpriseAPIError):
        await refund_service.cancel_scheduled_refund(seeded, refund.id, "again")


async def test_pending_refund_result_does_not_complete(seeded):
    """API success with a still-pending refund must not look resolved."""
    simulation_state.refund_result_status = RefundStatus.PENDING
    refund = await _issue(seeded)
    assert refund.status == RefundStatus.PENDING
    assert refund.completed_at is None

    txn = await ledger_service.get_transaction(seeded, "TXN_REFUND_FAILURE")
    assert Decimal(txn.refunded_amount) == Decimal("0.00")


async def test_repeated_failures_exhaust_the_arm_counter(seeded):
    simulation_state.fail_next_refund_attempts = 2
    for _ in range(2):
        with pytest.raises(EnterpriseAPIError):
            await _issue(seeded)
    refund = await _issue(seeded)
    assert refund.status == RefundStatus.COMPLETED
    assert refund.attempt_count == 3
