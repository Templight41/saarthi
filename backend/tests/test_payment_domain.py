"""The payment domain and the boundary a real platform would sit behind.

Two things are being protected here. First, that the adapter seam in
`services/providers.py` still describes what the services actually do — a
written-down interface nothing checks is just a comment. Second, and more
importantly, that a notification and a payment never become the same thing.
"""

from __future__ import annotations

import importlib
import inspect
from decimal import Decimal

import pytest

from saarthi.database.enums import (
    UNSETTLED_SETTLEMENT,
    NotificationChannel,
    PaymentStatus,
    ReconciliationOutcome,
    SettlementStatus,
)
from saarthi.database.models import Merchant, Settlement, Transaction
from saarthi.services import ledger_service, notification_service
from saarthi.services.providers import IMPLEMENTATIONS

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# The adapter boundary
# --------------------------------------------------------------------------
def test_every_protocol_has_something_implementing_it():
    for name, (protocol, module_path) in IMPLEMENTATIONS.items():
        module = importlib.import_module(module_path)
        for member in _protocol_members(protocol):
            assert hasattr(module, member), f"{name}: {module_path} is missing {member}()"
            assert callable(getattr(module, member)), f"{name}: {member} is not callable"


def test_the_protocols_still_describe_what_the_services_do():
    """Signature drift, not just missing names.

    A protocol that has quietly stopped matching its implementation is worse
    than no protocol, because it reads like a guarantee.
    """
    for name, (protocol, module_path) in IMPLEMENTATIONS.items():
        module = importlib.import_module(module_path)
        for member in _protocol_members(protocol):
            expected = [
                p
                for p in inspect.signature(getattr(protocol, member)).parameters
                if p != "self"
            ]
            actual = list(inspect.signature(getattr(module, member)).parameters)
            missing = [p for p in expected if p not in actual]
            assert not missing, f"{name}.{member} does not accept {missing}"


def test_every_write_that_moves_money_takes_an_idempotency_key():
    from saarthi.services import refund_service

    signature = inspect.signature(refund_service.issue_refund)
    assert "idempotency_key" in signature.parameters


def _protocol_members(protocol: type) -> list[str]:
    return [
        name
        for name in getattr(protocol, "__protocol_attrs__", dir(protocol))
        if not name.startswith("_")
    ]


# --------------------------------------------------------------------------
# A notification is evidence, not a payment
# --------------------------------------------------------------------------
async def _merchant(session, merchant_id: str = "M8001") -> Merchant:
    merchant = Merchant(id=merchant_id, name="Test Stall", email="t@example.in")
    session.add(merchant)
    await session.flush()
    return merchant


async def test_an_announcement_the_ledger_confirms(session):
    await _merchant(session)
    session.add(
        Transaction(
            id="TXN8001",
            merchant_id="M8001",
            amount=Decimal("240.00"),
            payment_status=PaymentStatus.SUCCESS,
        )
    )
    await session.flush()

    event = await notification_service.record_announcement(
        session, merchant_id="M8001", reference="TXN8001", announced_amount=Decimal("240.00")
    )
    result = await notification_service.reconcile(session, event)

    assert result.outcome is ReconciliationOutcome.MATCHED_SUCCESS
    assert result.confirmed is True
    # Even a confirmed announcement says it is not authoritative. The ledger
    # row it points at is.
    assert result.as_dict()["authoritative"] is False


async def test_an_announcement_for_a_payment_that_does_not_exist(session):
    """The case Saarthi must not resolve by inventing a transaction."""
    await _merchant(session)
    event = await notification_service.record_announcement(
        session, merchant_id="M8001", reference="TXN-NOPE", announced_amount=Decimal("500.00")
    )
    result = await notification_service.reconcile(session, event)

    assert result.outcome is ReconciliationOutcome.NO_AUTHORITATIVE_RECORD
    assert result.transaction_id is None
    assert result.confirmed is False


async def test_an_announcement_ahead_of_the_bank(session):
    await _merchant(session)
    session.add(
        Transaction(
            id="TXN8002",
            merchant_id="M8001",
            amount=Decimal("180.00"),
            payment_status=PaymentStatus.PAYMENT_PENDING,
        )
    )
    await session.flush()
    event = await notification_service.record_announcement(
        session, merchant_id="M8001", reference="TXN8002", announced_amount=Decimal("180.00")
    )

    result = await notification_service.reconcile(session, event)
    assert result.outcome is ReconciliationOutcome.MATCHED_PENDING
    assert result.confirmed is False


async def test_an_announcement_for_the_wrong_amount(session):
    await _merchant(session)
    session.add(
        Transaction(
            id="TXN8003",
            merchant_id="M8001",
            amount=Decimal("240.00"),
            payment_status=PaymentStatus.SUCCESS,
        )
    )
    await session.flush()
    event = await notification_service.record_announcement(
        session, merchant_id="M8001", reference="TXN8003", announced_amount=Decimal("2400.00")
    )

    result = await notification_service.reconcile(session, event)
    assert result.outcome is ReconciliationOutcome.AMOUNT_MISMATCH
    assert result.confirmed is False


async def test_reconciling_never_writes(session):
    """A merchant insisting they heard it cannot bring a payment into being."""
    await _merchant(session)
    event = await notification_service.record_announcement(
        session, merchant_id="M8001", reference="TXN-GHOST", announced_amount=Decimal("500.00")
    )
    await session.flush()

    await notification_service.reconcile(session, event)
    await notification_service.reconcile(session, event)

    from sqlalchemy import func, select

    count = await session.scalar(select(func.count()).select_from(Transaction))
    assert count == 0


async def test_announcements_carry_their_channel_and_device(session):
    await _merchant(session)
    event = await notification_service.record_announcement(
        session,
        merchant_id="M8001",
        reference="TXN8004",
        channel=NotificationChannel.SMS,
        device_id="SB-1234",
    )
    assert event.channel is NotificationChannel.SMS
    assert event.device_id == "SB-1234"
    assert event.id.startswith("NTF-")


# --------------------------------------------------------------------------
# Settlement lifecycle
# --------------------------------------------------------------------------
async def test_an_overdue_settlement_still_counts_as_unsettled(session):
    """OVERDUE is a more precise PENDING, not a different outcome.

    Every place that used to ask `== PENDING` must accept it, or a settlement
    quietly stops being late the moment we notice that it is.
    """
    assert SettlementStatus.PENDING in UNSETTLED_SETTLEMENT
    assert SettlementStatus.OVERDUE in UNSETTLED_SETTLEMENT
    assert SettlementStatus.COMPLETED not in UNSETTLED_SETTLEMENT
    assert SettlementStatus.FAILED not in UNSETTLED_SETTLEMENT

    await _merchant(session, "M8002")
    session.add(
        Transaction(
            id="TXN8005",
            merchant_id="M8002",
            amount=Decimal("100.00"),
            payment_status=PaymentStatus.PAYMENT_PENDING,
            customer_debited=True,
        )
    )
    await session.flush()
    from datetime import timedelta

    from saarthi.database.database import utcnow

    session.add(
        Settlement(
            id="STL-8005",
            transaction_id="TXN8005",
            status=SettlementStatus.OVERDUE,
            expected_at=utcnow() - timedelta(hours=3),
            monitor_armed=True,
        )
    )
    await session.flush()

    eta = await ledger_service.get_settlement_eta(session, "TXN8005")
    assert eta.overdue is True

    delayed = await ledger_service.find_delayed_settlements(session, grace_seconds=60)
    assert [row["transaction_id"] for row in delayed] == ["TXN8005"]


# --------------------------------------------------------------------------
# The clamp does not depend on the model agreeing
# --------------------------------------------------------------------------
def _diagnosis(**over):
    from saarthi.schemas.agent import Diagnosis, Intent, RootCause

    base = dict(
        intent=Intent.PAYMENT_DEBITED_BUT_NOT_CONFIRMED,
        transaction_id="TXN_SOUNDBOX_PENDING",
        root_cause=RootCause.SETTLEMENT_DELAY,
        confidence=0.9,
        risk="LOW",
        summary="",
    )
    return Diagnosis(**{**base, **over})


def _context(notifications):
    from saarthi.agent.context import CaseContext

    return CaseContext(
        case_id="CASE-1",
        merchant={"id": "M1003", "autonomous_refund_limit": "1000.00"},
        transaction={
            "id": "TXN_SOUNDBOX_PENDING",
            "amount": "180.00",
            "payment_status": "PAYMENT_PENDING",
            "customer_debited": True,
        },
        settlement={"status": "PENDING"},
        notifications=notifications,
    )


def _phantom(reference="TXN20455"):
    return {
        "reference": reference,
        "outcome": "NO_AUTHORITATIVE_RECORD",
        "confirmed_by_ledger": False,
    }


def test_a_phantom_announcement_forces_a_human_whatever_the_model_said():
    """The live regression this test exists for.

    Gemini classified a Soundbox complaint as a settlement delay about the
    nearby pending transaction, which is a reasonable reading and silently
    drops the announcement with no payment behind it. A safety behaviour must
    not depend on the model picking the right label.
    """
    from saarthi.agent.diagnosis import clamp_to_facts
    from saarthi.schemas.agent import Intent, RootCause

    clamped = clamp_to_facts(_diagnosis(), _context([_phantom()]))

    assert clamped.root_cause is RootCause.ANNOUNCEMENT_WITHOUT_PAYMENT
    assert clamped.intent is Intent.NOTIFICATION_MISMATCH
    assert clamped.requires_human is True
    assert {"root_cause", "intent", "requires_human"} <= set(clamped.clamped_fields)


def test_a_confirmed_announcement_does_not_force_anything():
    from saarthi.agent.diagnosis import clamp_to_facts
    from saarthi.schemas.agent import RootCause

    confirmed = {
        "reference": "TXN_SOUNDBOX_OK",
        "outcome": "MATCHED_SUCCESS",
        "confirmed_by_ledger": True,
    }
    clamped = clamp_to_facts(_diagnosis(), _context([confirmed]))

    assert clamped.root_cause is RootCause.SETTLEMENT_DELAY
    assert clamped.requires_human is False


def test_an_announcement_the_identified_transaction_explains_is_not_a_phantom():
    """Otherwise the case about a transaction would escalate over itself."""
    from saarthi.agent.diagnosis import clamp_to_facts
    from saarthi.schemas.agent import RootCause

    clamped = clamp_to_facts(
        _diagnosis(), _context([_phantom(reference="TXN_SOUNDBOX_PENDING")])
    )
    assert clamped.root_cause is RootCause.SETTLEMENT_DELAY
