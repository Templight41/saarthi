from __future__ import annotations

import pytest

from saarthi.agent.state_machine import ALLOWED, IllegalTransition, is_allowed, transition
from saarthi.database.enums import CaseStatus, Resolution
from saarthi.database.models import Case


async def _case(session, status: CaseStatus) -> Case:
    case = Case(id=f"CASE-T{status.value[:4]}", merchant_id="M1001", status=status)
    session.add(case)
    await session.flush()
    return case


def test_every_declared_edge_is_allowed():
    for source, targets in ALLOWED.items():
        for target in targets:
            assert is_allowed(source, target), f"{source} -> {target} should be allowed"


def test_resolution_requires_verification():
    """ACTING cannot reach RESOLVED: verification is structurally mandatory."""
    assert not is_allowed(CaseStatus.ACTING, CaseStatus.RESOLVED)


def test_failure_must_attempt_recovery_before_escalating():
    assert not is_allowed(CaseStatus.VERIFYING, CaseStatus.ESCALATED)
    assert is_allowed(CaseStatus.VERIFYING, CaseStatus.RECOVERING)
    assert is_allowed(CaseStatus.RECOVERING, CaseStatus.ESCALATED)


def test_diagnosis_cannot_skip_policy_check():
    assert not is_allowed(CaseStatus.DIAGNOSING, CaseStatus.ACTING)


def test_resolved_is_terminal():
    assert ALLOWED[CaseStatus.RESOLVED] == frozenset()


@pytest.mark.asyncio
async def test_transition_writes_state_changed_event(seeded):
    case = await _case(seeded, CaseStatus.RECEIVED)
    event = await transition(seeded, case, CaseStatus.IDENTIFYING, "identifying merchant")
    assert case.status == CaseStatus.IDENTIFYING
    assert event.event_type == "STATE_CHANGED"
    assert event.meta["from"] == "RECEIVED"
    assert event.meta["to"] == "IDENTIFYING"
    assert "identifying merchant" in event.message


@pytest.mark.asyncio
async def test_transition_to_resolved_sets_timestamp_and_emits_event(seeded):
    case = await _case(seeded, CaseStatus.VERIFYING)
    case.resolution = Resolution.AUTONOMOUS
    await transition(seeded, case, CaseStatus.RESOLVED, "verified")
    assert case.resolved_at is not None
    assert case.wait_reason is None

    from saarthi.agent.events import event_types_for_case

    types = await event_types_for_case(seeded, case.id)
    assert "CASE_RESOLVED" in types


@pytest.mark.asyncio
async def test_illegal_transition_raises(seeded):
    case = await _case(seeded, CaseStatus.ACTING)
    with pytest.raises(IllegalTransition):
        await transition(seeded, case, CaseStatus.RESOLVED, "skipping verification")


@pytest.mark.asyncio
async def test_terminal_case_cannot_move(seeded):
    case = await _case(seeded, CaseStatus.RESOLVED)
    with pytest.raises(IllegalTransition):
        await transition(seeded, case, CaseStatus.ACTING, "reopening")
