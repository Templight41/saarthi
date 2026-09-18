"""Explicit case state machine.

The model never invents a workflow state. The transition table below is the
whole set of legal moves, and two forbidden edges carry most of the product's
meaning:

  ACTING -> RESOLVED      is illegal, so nothing resolves without verification.
  VERIFYING -> ESCALATED  is illegal, so a failure must attempt recovery first
                          and that attempt is always auditable.

Waiting is not a transition. A case parks by leaving its status unchanged and
having the handler return no next state.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import Actor, CaseStatus
from ..database.models import AgentEvent, Case
from .events import EventType, record_event

ALLOWED: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.RECEIVED: frozenset({CaseStatus.IDENTIFYING}),
    CaseStatus.IDENTIFYING: frozenset({CaseStatus.INVESTIGATING, CaseStatus.ESCALATED}),
    CaseStatus.INVESTIGATING: frozenset({CaseStatus.DIAGNOSING, CaseStatus.ESCALATED}),
    CaseStatus.DIAGNOSING: frozenset({CaseStatus.POLICY_CHECK, CaseStatus.ESCALATED}),
    CaseStatus.POLICY_CHECK: frozenset({CaseStatus.PLANNING, CaseStatus.ESCALATED}),
    CaseStatus.PLANNING: frozenset({CaseStatus.ACTING, CaseStatus.ESCALATED}),
    CaseStatus.ACTING: frozenset({CaseStatus.VERIFYING, CaseStatus.RECOVERING}),
    CaseStatus.VERIFYING: frozenset(
        {CaseStatus.RESOLVED, CaseStatus.RECOVERING, CaseStatus.POLICY_CHECK}
    ),
    CaseStatus.RECOVERING: frozenset({CaseStatus.ACTING, CaseStatus.VERIFYING, CaseStatus.ESCALATED}),
    CaseStatus.ESCALATED: frozenset({CaseStatus.ACTING, CaseStatus.RESOLVED}),
    CaseStatus.RESOLVED: frozenset(),
}

TERMINAL = frozenset({CaseStatus.RESOLVED})
PARKABLE = frozenset({CaseStatus.VERIFYING, CaseStatus.ESCALATED})


class IllegalTransition(RuntimeError):
    def __init__(self, case_id: str, from_state: CaseStatus, to_state: CaseStatus) -> None:
        super().__init__(
            f"Illegal transition for {case_id}: {from_state.value} -> {to_state.value}"
        )
        self.case_id = case_id
        self.from_state = from_state
        self.to_state = to_state


def is_allowed(from_state: CaseStatus, to_state: CaseStatus) -> bool:
    return to_state in ALLOWED[from_state]


async def transition(
    session: AsyncSession,
    case: Case,
    new_state: CaseStatus,
    reason: str,
    *,
    actor: Actor = Actor.SAARTHI,
    meta: dict | None = None,
) -> AgentEvent:
    previous = case.status
    if not is_allowed(previous, new_state):
        raise IllegalTransition(case.id, previous, new_state)

    case.status = new_state
    case.updated_at = utcnow()
    if new_state in TERMINAL:
        case.resolved_at = utcnow()
        case.wait_reason = None
        case.current_action = None

    event = await record_event(
        session,
        case,
        EventType.STATE_CHANGED,
        actor=actor,
        message=f"{previous.value} → {new_state.value}: {reason}",
        meta={"from": previous.value, "to": new_state.value, "reason": reason, **(meta or {})},
    )

    # A terminal state also emits its own semantic event for the timeline.
    if new_state == CaseStatus.RESOLVED:
        duration = None
        if case.resolved_at and case.created_at:
            duration = int((case.resolved_at - case.created_at).total_seconds())
        await record_event(
            session,
            case,
            EventType.CASE_RESOLVED,
            actor=actor,
            message="Case resolved",
            result={
                "resolution": case.resolution.value if case.resolution else None,
                "duration_seconds": duration,
            },
        )
    elif new_state == CaseStatus.ESCALATED:
        await record_event(
            session,
            case,
            EventType.CASE_ESCALATED,
            actor=actor,
            message=f"Case escalated: {reason}",
            meta={"reason": reason},
        )
    return event
