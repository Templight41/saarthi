"""End-to-end scenario tests.

These are the acceptance gate. Each one drives the full autonomous loop and
asserts on the business state and the audit trail, not on prose.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio

from saarthi.agent.events import list_events
from saarthi.database.enums import CaseOrigin, CaseStatus, EscalationStatus, RefundStatus, Resolution
from saarthi.database.ids import next_id
from saarthi.database.models import Action, Case
from saarthi.database.seed import seed_all
from saarthi.memory.knowledge import seed_knowledge
from saarthi.runtime import SaarthiRuntime
from saarthi.services import ledger_service, refund_service
from saarthi.simulation.failure_injection import simulation_state

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def runtime(settings, session_factory) -> SaarthiRuntime:
    rt = SaarthiRuntime.build(settings, session_factory=session_factory)
    async with session_factory() as session:
        await seed_all(session)
        await seed_knowledge(session, rt.memory)
        await session.commit()
    return rt


async def _open_case(runtime, message: str, *, transaction_id: str | None = None) -> str:
    async with runtime.session_factory() as session:
        case = Case(
            id=await next_id(session, "case"),
            merchant_id="M1001",
            transaction_id=transaction_id,
            status=CaseStatus.RECEIVED,
            origin=CaseOrigin.MERCHANT_CHAT,
            original_message=message,
        )
        session.add(case)
        await session.flush()
        from saarthi.agent.events import EventType, record_event
        from saarthi.database.enums import Actor

        await record_event(
            session, case, EventType.CASE_CREATED, actor=Actor.MERCHANT, message="New merchant case received"
        )
        await record_event(
            session, case, EventType.MESSAGE_RECEIVED, actor=Actor.MERCHANT, message=message
        )
        await session.commit()
        case_id = case.id
    await runtime.runner.start(case_id)
    return case_id


async def _event_types(runtime, case_id: str) -> list[str]:
    async with runtime.session_factory() as session:
        return [e.event_type for e in await list_events(session, case_id)]


async def _case(runtime, case_id: str) -> Case:
    async with runtime.session_factory() as session:
        return await session.get(Case, case_id)


# ==========================================================================
# Scenario A — settlement delay
# ==========================================================================
async def test_scenario_a_parks_awaiting_settlement(runtime):
    case_id = await _open_case(
        runtime, "My customer's payment failed, but the money was deducted.", transaction_id="TXN18293"
    )
    case = await _case(runtime, case_id)

    assert case.status == CaseStatus.VERIFYING
    assert case.wait_reason == "SETTLEMENT_PENDING"
    assert case.intent == "PAYMENT_DEBITED_BUT_NOT_CONFIRMED"
    assert case.diagnosis["root_cause"] == "SETTLEMENT_DELAY"

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(session, "TXN18293")
    assert len(refunds) == 1
    assert refunds[0].status == RefundStatus.SCHEDULED, "a standby refund must be armed"

    types = await _event_types(runtime, case_id)
    for expected in [
        "MERCHANT_IDENTIFIED",
        "TRANSACTION_RETRIEVED",
        "SETTLEMENT_CHECKED",
        "MEMORY_RETRIEVED",
        "DIAGNOSIS_COMPLETE",
        "POLICY_CHECKED",
        "PLAN_CREATED",
        "REFUND_SCHEDULED",
        "MESSAGE_SENT",
        "VERIFICATION_PENDING",
    ]:
        assert expected in types, f"{expected} missing from the timeline"
    assert "CASE_RESOLVED" not in types, "must not resolve while settlement is unknown"


async def test_scenario_a_resolves_when_settlement_completes(runtime):
    case_id = await _open_case(
        runtime, "My customer's payment failed, but the money was deducted.", transaction_id="TXN18293"
    )

    async with runtime.session_factory() as session:
        await ledger_service.complete_settlement(session, "TXN18293")
        await session.commit()
    await runtime.runner.resume(case_id, trigger="SETTLEMENT_UPDATE")

    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.RESOLVED
    assert case.resolution == Resolution.AUTONOMOUS

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(session, "TXN18293")
    assert refunds[0].status == RefundStatus.CANCELLED, "standby refund must stand down"

    types = await _event_types(runtime, case_id)
    assert "SETTLEMENT_VERIFIED" in types
    assert "CASE_RESOLVED" in types
    assert "ACTION_FAILED" not in types


async def test_scenario_a_refunds_when_settlement_fails(runtime):
    case_id = await _open_case(
        runtime, "My customer's payment failed, but the money was deducted.", transaction_id="TXN18293"
    )

    async with runtime.session_factory() as session:
        await ledger_service.fail_settlement(session, "TXN18293", "BANK_REJECTED")
        await session.commit()
    await runtime.runner.resume(case_id, trigger="SETTLEMENT_UPDATE")

    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.RESOLVED

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(session, "TXN18293")
    completed = [r for r in refunds if r.status == RefundStatus.COMPLETED]
    assert len(completed) == 1
    assert Decimal(completed[0].amount) == Decimal("3200.00")


# ==========================================================================
# Scenario B — refund API failure and recovery
# ==========================================================================
async def test_scenario_b_recovers_from_refund_failure(runtime):
    simulation_state.fail_next_refund_attempts = 1

    case_id = await _open_case(
        runtime,
        "The customer cancelled order A-5521 and wants the ₹2,500 refunded.",
        transaction_id="TXN_REFUND_FAILURE",
    )
    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.RESOLVED
    assert case.resolution == Resolution.AUTONOMOUS

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(session, "TXN_REFUND_FAILURE")
    assert len(refunds) == 1, "recovery must never create a second refund"
    assert refunds[0].status == RefundStatus.COMPLETED
    assert refunds[0].attempt_count == 2

    types = await _event_types(runtime, case_id)
    order = {name: i for i, name in enumerate(types) if name in {
        "ACTION_FAILED",
        "RECOVERY_STARTED",
        "REFUND_STATE_CHECKED",
        "ACTION_RETRIED",
        "ACTION_VERIFIED",
        "CASE_RESOLVED",
    }}
    assert order["ACTION_FAILED"] < order["RECOVERY_STARTED"]
    assert order["RECOVERY_STARTED"] < order["REFUND_STATE_CHECKED"]
    assert order["REFUND_STATE_CHECKED"] < order["ACTION_RETRIED"]
    assert order["ACTION_RETRIED"] < order["ACTION_VERIFIED"]
    assert order["ACTION_VERIFIED"] < order["CASE_RESOLVED"]


async def test_scenario_b_records_one_action_row_per_attempt(runtime):
    simulation_state.fail_next_refund_attempts = 1
    case_id = await _open_case(
        runtime,
        "The customer cancelled order A-5521 and wants the ₹2,500 refunded.",
        transaction_id="TXN_REFUND_FAILURE",
    )
    async with runtime.session_factory() as session:
        from sqlalchemy import select

        rows = await session.scalars(
            select(Action).where(Action.case_id == case_id, Action.action_type == "issue_refund")
        )
        attempts = sorted(r.attempt for r in rows)
    assert attempts == [1, 2], "each attempt must leave its own audit row"


# ==========================================================================
# Scenario C — high-value subjective dispute
# ==========================================================================
async def test_scenario_c_escalates_without_refunding(runtime):
    case_id = await _open_case(
        runtime,
        "The customer says the product quality was poor and wants a ₹15,000 partial refund.",
        transaction_id="TXN_HIGH_VALUE_DISPUTE",
    )
    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.ESCALATED
    assert case.requires_human is True
    assert case.human_required_by_policy is True, "policy-mandated review is not an autonomy failure"

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(
            session, "TXN_HIGH_VALUE_DISPUTE"
        )
    assert refunds == [], "Saarthi must not autonomously refund a high-value subjective dispute"

    async with runtime.session_factory() as session:
        escalation = await runtime.escalation.pending_for_case(session, case_id)
    assert escalation is not None
    assert escalation.reason == "HIGH_VALUE_SUBJECTIVE_DISPUTE"
    assert Decimal(escalation.amount) == Decimal("15000.00")
    assert escalation.recommendation == "REVIEW_PARTIAL_REFUND"
    assert escalation.completed_actions, "an escalation must carry the checks already done"
    assert escalation.pending_action["tool"] == "issue_refund"
    assert "POL-REFUND-LIMIT" in escalation.policy["policy_ids"]
    assert "POL-SUBJECTIVE-DISPUTE" in escalation.policy["policy_ids"]


async def test_scenario_c_approval_executes_and_verifies(runtime):
    case_id = await _open_case(
        runtime,
        "The customer says the product quality was poor and wants a ₹15,000 partial refund.",
        transaction_id="TXN_HIGH_VALUE_DISPUTE",
    )
    async with runtime.session_factory() as session:
        escalation = await runtime.escalation.pending_for_case(session, case_id)
        from datetime import UTC, datetime

        escalation.status = EscalationStatus.APPROVED
        escalation.human_decision = {"decision": "APPROVE", "note": "goodwill"}
        escalation.decided_by = "ops@urbanthreads.in"
        case = await session.get(Case, case_id)
        case.human_override = {
            "escalation_id": escalation.id,
            "decided_by": "ops@urbanthreads.in",
            "decided_at": datetime.now(UTC).isoformat(),
            "note": "goodwill",
        }
        await session.commit()

    await runtime.runner.resume(case_id, trigger="HUMAN_APPROVED")

    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.RESOLVED
    assert case.resolution == Resolution.HUMAN_APPROVED

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(
            session, "TXN_HIGH_VALUE_DISPUTE"
        )
        events = await list_events(session, case_id)
    assert len(refunds) == 1
    assert refunds[0].status == RefundStatus.COMPLETED

    approval_events = [
        e for e in events if e.event_type == "POLICY_CHECKED" and e.result.get("policy_ids")
    ]
    assert any(
        e.result["policy_ids"][0] == "APPROVED_BY_HUMAN" for e in approval_events
    ), "the override must be visible in the audit trail"


# ==========================================================================
# Scenario D — nothing is wrong
# ==========================================================================
async def test_scenario_d_takes_no_unnecessary_action(runtime):
    case_id = await _open_case(
        runtime,
        "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
        transaction_id="TXN_NORMAL_SUCCESS",
    )
    case = await _case(runtime, case_id)
    assert case.status == CaseStatus.RESOLVED

    async with runtime.session_factory() as session:
        refunds = await refund_service.list_refunds_for_transaction(session, "TXN_NORMAL_SUCCESS")
    assert refunds == [], "a healthy transaction needs no financial action"

    types = await _event_types(runtime, case_id)
    assert "MESSAGE_SENT" in types
    assert "ACTION_FAILED" not in types
    assert "ESCALATION_CREATED" not in types


# ==========================================================================
# Audit trail contract
# ==========================================================================
async def test_every_event_is_machine_readable(runtime):
    from saarthi.agent.events import EventType

    known = {e.value for e in EventType}
    case_id = await _open_case(
        runtime, "My customer's payment failed, but the money was deducted.", transaction_id="TXN18293"
    )
    async with runtime.session_factory() as session:
        events = await list_events(session, case_id)

    assert events
    for event in events:
        assert event.event_type in known, f"unknown event type {event.event_type}"
        assert event.message.strip(), f"{event.event_type} has no message"
        assert event.status is not None
