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
from saarthi.services import ledger_service, notification_service, ops_service
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


# --------------------------------------------------------------------------
# Which transaction the merchant meant
# --------------------------------------------------------------------------
async def _ledger(session):
    """One merchant, three transactions: two pending, one disputed."""
    from datetime import timedelta

    from saarthi.database.database import utcnow
    from saarthi.database.enums import DisputeStatus, DisputeType
    from saarthi.database.models import Dispute

    await _merchant(session, "M9100")
    now = utcnow()
    rows = [
        ("TXN_PENDING_A", Decimal("3200.00"), PaymentStatus.PAYMENT_PENDING, SettlementStatus.PENDING),
        ("TXN_PENDING_B", Decimal("4800.00"), PaymentStatus.PAYMENT_PENDING, SettlementStatus.PENDING),
        ("TXN_DISPUTED", Decimal("15000.00"), PaymentStatus.SUCCESS, SettlementStatus.COMPLETED),
        ("TXN_SETTLED", Decimal("2500.00"), PaymentStatus.SUCCESS, SettlementStatus.COMPLETED),
    ]
    for index, (txn_id, amount, pay, stl) in enumerate(rows):
        session.add(
            Transaction(
                id=txn_id,
                merchant_id="M9100",
                amount=amount,
                payment_status=pay,
                customer_debited=True,
                created_at=now - timedelta(minutes=index),
            )
        )
        await session.flush()
        session.add(
            Settlement(id=f"STL-91{index}", transaction_id=txn_id, status=stl, expected_at=now)
        )
    session.add(
        Dispute(
            id="DSP-910",
            transaction_id="TXN_DISPUTED",
            type=DisputeType.PRODUCT_QUALITY,
            status=DisputeStatus.OPEN,
            requested_amount=Decimal("15000.00"),
        )
    )
    await session.flush()


async def test_a_pending_payment_question_never_lands_on_a_dispute(session):
    """The bug this rewrite exists for.

    Two pending payments meant the "exactly one pending" rule failed, and the
    cascade then fell through to "exactly one disputed" — answering a question
    about a pending payment with an unrelated ₹15,000 quality dispute.
    """
    from saarthi.agent.identify import resolve_transaction

    await _ledger(session)
    resolved, how, candidates = await resolve_transaction(
        session, "M9100", "Customer paid but the transaction is still pending"
    )

    assert resolved != "TXN_DISPUTED"
    assert how == "AMBIGUOUS"
    # Both pending payments are offered to the person who has to decide.
    assert set(candidates) == {"TXN_PENDING_A", "TXN_PENDING_B"}


async def test_the_amount_the_merchant_named_decides_it(session):
    from saarthi.agent.identify import resolve_transaction

    await _ledger(session)
    resolved, how, _ = await resolve_transaction(
        session, "M9100", "Customer paid ₹3,200 but it is still pending"
    )
    assert (resolved, how) == ("TXN_PENDING_A", "AMOUNT")


async def test_an_amount_that_contradicts_the_symptom_is_not_guessed(session):
    """₹2,500 is settled; the pending payments are other amounts. Two
    transactions each fit half of what was said, so neither is chosen."""
    from saarthi.agent.identify import resolve_transaction

    await _ledger(session)
    resolved, how, candidates = await resolve_transaction(
        session, "M9100", "Customer paid ₹2,500 but the transaction is pending"
    )
    assert resolved is None
    assert how == "AMBIGUOUS"
    assert "TXN_SETTLED" in candidates


async def test_a_dispute_question_still_finds_the_dispute(session):
    from saarthi.agent.identify import resolve_transaction

    await _ledger(session)
    resolved, how, _ = await resolve_transaction(
        session, "M9100", "The customer says the product quality was poor"
    )
    assert (resolved, how) == ("TXN_DISPUTED", "TOPIC")


async def test_an_explicit_id_always_wins(session):
    from saarthi.agent.identify import resolve_transaction

    await _ledger(session)
    resolved, how, _ = await resolve_transaction(
        session, "M9100", "Something is wrong with TXN_DISPUTED", hint=None
    )
    assert (resolved, how) == ("TXN_DISPUTED", "REGEX")


# --------------------------------------------------------------------------
# Languages: understood is a bigger set than speakable
# --------------------------------------------------------------------------
def test_every_speakable_language_is_also_understood():
    from saarthi.voice import languages

    assert languages.SPEAKABLE < languages.UNDERSTOOD
    assert len(languages.SPEAKABLE) == 11


def test_a_language_saarthi_can_write_but_not_say():
    from saarthi.voice import languages

    assert languages.is_understood("as-IN") is True
    assert languages.is_speakable("as-IN") is False
    assert languages.is_speakable("ta-IN") is True


def test_an_unknown_language_becomes_auto_detect_rather_than_an_error():
    from saarthi.voice import languages

    assert languages.for_transcription(None) == languages.AUTO
    assert languages.for_transcription("") == languages.AUTO
    assert languages.for_transcription("fr-FR") == languages.AUTO
    assert languages.for_transcription("ta-IN") == "ta-IN"


async def test_the_case_language_overrides_the_merchants_default(session):
    """A merchant served in Hindi can still raise one case in Tamil."""
    from saarthi.agent.messaging import _merchant_language
    from saarthi.database.models import Case
    from saarthi.tools.registry import ToolContext

    merchant = await _merchant(session, "M9200")
    merchant.language = "hi-IN"
    await session.flush()

    case = Case(id="CASE-L1", merchant_id="M9200", original_message="x")
    ctx = ToolContext(session=session, case=case, simulation=None, runtime=None)

    assert await _merchant_language(ctx) == "hi-IN"

    case.language = "ta-IN"
    assert await _merchant_language(ctx) == "ta-IN"

    # Something neither provider knows falls back rather than reaching bulbul.
    case.language = "fr-FR"
    assert await _merchant_language(ctx) == "en-IN"


async def test_speech_refuses_a_language_bulbul_cannot_say(client):
    """415, not a wrong-voice reading of the text."""
    created = await client.post(
        "/api/cases",
        json={
            "message": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
            "transaction_id": "TXN_NORMAL_SUCCESS",
        },
    )
    case_id = created.json()["id"]
    messages = (await client.get(f"/api/cases/{case_id}/messages")).json()["messages"]
    outbound = next(m for m in messages if m["direction"] == "OUTBOUND")

    async with client.runtime.session_factory() as db:
        row = await ops_service.get_message(db, outbound["id"])
        row.meta = {**row.meta, "language": "as-IN"}
        await db.commit()

    response = await client.get(f"/api/voice/messages/{outbound['id']}/speech")
    assert response.status_code == 415
    assert "Assamese" in response.json()["detail"]


async def test_the_language_catalogue_is_served(client):
    body = (await client.get("/api/voice/languages")).json()
    codes = {lang["code"] for lang in body["languages"]}

    assert {"en-IN", "hi-IN", "ta-IN", "as-IN", "sat-IN"} <= codes
    assert body["auto"] == "unknown"
    # The selector needs to know which ones come with audio.
    assert any(lang["speakable"] for lang in body["languages"])
    assert any(not lang["speakable"] for lang in body["languages"])


# --------------------------------------------------------------------------
# Merchants do not write in English
# --------------------------------------------------------------------------
def test_hinglish_and_devanagari_are_understood():
    """A live case read "customer के bank से cut हुआ है" as describing nothing,
    because every keyword list was English-only."""
    from saarthi.agent.identify import topics_in

    hinglish = (
        "customer ने 4800 का transaction किया है but bank में नहीं आया है "
        "लेकिन customer के bank से cut हुआ है।"
    )
    assert topics_in(hinglish) == {"PENDING"}
    assert topics_in("ग्राहक ने रिफंड मांगा है") == {"REFUND"}
    assert topics_in("सामान खराब निकला") == {"DISPUTE"}
    assert topics_in("paisa nahi aaya") == {"PENDING"}
    # The English word inside a Hindi sentence still counts.
    assert "PENDING" in topics_in("payment cut हो गया")


async def test_two_payments_of_the_same_amount_are_narrowed_not_abandoned(session):
    """The amount is still the best signal even when it is not unique."""
    from datetime import timedelta

    from saarthi.agent.identify import resolve_transaction
    from saarthi.database.database import utcnow

    await _merchant(session, "M9300")
    now = utcnow()
    for index, (txn_id, pay, stl) in enumerate(
        [
            ("TXN_SAME_PENDING", PaymentStatus.PAYMENT_PENDING, SettlementStatus.PENDING),
            ("TXN_SAME_DONE", PaymentStatus.SUCCESS, SettlementStatus.COMPLETED),
        ]
    ):
        session.add(
            Transaction(
                id=txn_id,
                merchant_id="M9300",
                amount=Decimal("4800.00"),
                payment_status=pay,
                customer_debited=True,
                created_at=now - timedelta(minutes=index),
            )
        )
        await session.flush()
        session.add(
            Settlement(id=f"STL-93{index}", transaction_id=txn_id, status=stl, expected_at=now)
        )
    await session.flush()

    resolved, how, _ = await resolve_transaction(
        session, "M9300", "customer ने 4800 का transaction किया है but bank में नहीं आया है"
    )
    assert (resolved, how) == ("TXN_SAME_PENDING", "AMOUNT_NARROWED")


async def test_genuinely_identical_payments_still_ask(session):
    """Two unresolved payments for the same amount are not guessable."""
    from saarthi.agent.identify import resolve_transaction
    from saarthi.database.database import utcnow

    await _merchant(session, "M9301")
    now = utcnow()
    for index, txn_id in enumerate(["TXN_TWIN_A", "TXN_TWIN_B"]):
        session.add(
            Transaction(
                id=txn_id,
                merchant_id="M9301",
                amount=Decimal("4800.00"),
                payment_status=PaymentStatus.PAYMENT_PENDING,
                customer_debited=True,
            )
        )
        await session.flush()
        session.add(
            Settlement(
                id=f"STL-94{index}",
                transaction_id=txn_id,
                status=SettlementStatus.PENDING,
                expected_at=now,
            )
        )
    await session.flush()

    resolved, how, candidates = await resolve_transaction(
        session, "M9301", "4800 का payment नहीं आया"
    )
    assert resolved is None and how == "AMBIGUOUS"
    assert set(candidates) == {"TXN_TWIN_A", "TXN_TWIN_B"}


def test_the_drafting_prompt_forbids_inventing_account_states():
    """A live reply said "your account verification is currently on hold",
    which was not true of anything."""
    from saarthi.agent.messaging import SYSTEM_PROMPT

    assert "under verification" in SYSTEM_PROMPT
    assert "Never name an internal process" in SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Nothing internal reaches the merchant
# --------------------------------------------------------------------------
async def test_no_template_can_put_none_in_front_of_a_merchant():
    """A live reply opened "I've gathered everything on None".

    `facts.get("transaction_id", "the transaction")` looks safe, but the
    default only applies when the key is absent — and the escalation facts set
    it to None deliberately when no single transaction was identified.
    """
    from saarthi.llm.mock import MockProvider
    from saarthi.schemas.agent import MessageDraft

    provider = MockProvider()
    stages = [
        "FOLLOW_UP",
        "INTERIM",
        "SETTLED",
        "REFUNDED",
        "ESCALATED",
        "REJECTED",
        "NO_ISSUE",
        "OUTCOME",
    ]
    empty = {"transaction_id": None, "amount": None, "refund_id": None, "reason_text": None}

    for stage in stages:
        for facts in ({}, empty):
            draft = await provider.complete_json(
                system="",
                user="",
                schema=MessageDraft,
                context={"stage": stage, "facts": facts},
            )
            assert "None" not in draft.body, f"{stage} with {facts}: {draft.body}"
            assert "null" not in draft.body.lower(), f"{stage}: {draft.body}"


async def test_an_ambiguous_escalation_names_the_candidates():
    from saarthi.llm.mock import MockProvider
    from saarthi.schemas.agent import MessageDraft

    draft = await MockProvider().complete_json(
        system="",
        user="",
        schema=MessageDraft,
        context={
            "stage": "ESCALATED",
            "facts": {
                "transaction_id": None,
                "candidate_transactions": [
                    {"transaction_id": "TXN19931", "amount": "4800.00"},
                    {"transaction_id": "TXN20000", "amount": "4800.00"},
                ],
            },
        },
    )
    assert "TXN19931" in draft.body and "TXN20000" in draft.body
    assert "None" not in draft.body
