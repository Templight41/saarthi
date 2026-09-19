"""Audit trail.

Every significant decision writes one row here, and the frontend timeline is
driven entirely by these rows. Each event carries a machine-readable type and
status so the UI never has to parse prose to work out what happened.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import Actor, EventStatus
from ..database.ids import next_id
from ..database.models import AgentEvent, Case


class EventType(StrEnum):
    # Lifecycle
    CASE_CREATED = "CASE_CREATED"
    MESSAGE_RECEIVED = "MESSAGE_RECEIVED"
    STATE_CHANGED = "STATE_CHANGED"
    CASE_RESUMED = "CASE_RESUMED"
    CASE_RESOLVED = "CASE_RESOLVED"
    CASE_ESCALATED = "CASE_ESCALATED"
    AGENT_ERROR = "AGENT_ERROR"

    # Perceive / investigate
    MERCHANT_IDENTIFIED = "MERCHANT_IDENTIFIED"
    TRANSACTION_IDENTIFIED = "TRANSACTION_IDENTIFIED"
    TRANSACTION_RETRIEVED = "TRANSACTION_RETRIEVED"
    SETTLEMENT_CHECKED = "SETTLEMENT_CHECKED"
    DISPUTE_CHECKED = "DISPUTE_CHECKED"

    # Remember
    MEMORY_RETRIEVED = "MEMORY_RETRIEVED"
    MEMORY_STORED = "MEMORY_STORED"
    PATTERN_DETECTED = "PATTERN_DETECTED"

    # Reason / control
    DIAGNOSIS_COMPLETE = "DIAGNOSIS_COMPLETE"
    LLM_FALLBACK = "LLM_FALLBACK"
    POLICY_CHECKED = "POLICY_CHECKED"
    PLAN_CREATED = "PLAN_CREATED"

    # Act
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_RETRIED = "ACTION_RETRIED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_SCHEDULED = "ACTION_SCHEDULED"
    TICKET_CREATED = "TICKET_CREATED"
    REFUND_SCHEDULED = "REFUND_SCHEDULED"
    MESSAGE_DRAFTED = "MESSAGE_DRAFTED"
    MESSAGE_SENT = "MESSAGE_SENT"

    # Verify
    ACTION_VERIFIED = "ACTION_VERIFIED"
    SETTLEMENT_VERIFIED = "SETTLEMENT_VERIFIED"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"

    # Recover
    RECOVERY_STARTED = "RECOVERY_STARTED"
    FAILURE_CLASSIFIED = "FAILURE_CLASSIFIED"
    REFUND_STATE_CHECKED = "REFUND_STATE_CHECKED"
    SIDE_EFFECT_CHECKED = "SIDE_EFFECT_CHECKED"
    RECOVERY_DECIDED = "RECOVERY_DECIDED"

    # Escalate / human
    ESCALATION_CREATED = "ESCALATION_CREATED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    HUMAN_REJECTED = "HUMAN_REJECTED"
    HUMAN_TAKEOVER = "HUMAN_TAKEOVER"

    # Workflow
    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_TICK = "WORKFLOW_TICK"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    WORKFLOW_DISPATCH_FAILED = "WORKFLOW_DISPATCH_FAILED"
    PROACTIVE_ALERT_CREATED = "PROACTIVE_ALERT_CREATED"


async def record_event(
    session: AsyncSession,
    case: Case,
    event_type: EventType,
    *,
    actor: Actor = Actor.SAARTHI,
    message: str = "",
    status: EventStatus = EventStatus.SUCCESS,
    input: dict | None = None,
    result: dict | None = None,
    meta: dict | None = None,
) -> AgentEvent:
    sequence = case.next_sequence
    case.next_sequence = sequence + 1
    event = AgentEvent(
        id=await next_id(session, "event"),
        case_id=case.id,
        event_type=event_type.value,
        actor=actor,
        status=status,
        message=message,
        input=input,
        result=result,
        meta=meta,
        sequence=sequence,
    )
    session.add(event)
    await session.flush()
    return event


async def list_events(session: AsyncSession, case_id: str) -> list[AgentEvent]:
    result = await session.scalars(
        select(AgentEvent).where(AgentEvent.case_id == case_id).order_by(AgentEvent.sequence, AgentEvent.id)
    )
    return list(result)


async def event_types_for_case(session: AsyncSession, case_id: str) -> set[str]:
    rows = await session.scalars(select(AgentEvent.event_type).where(AgentEvent.case_id == case_id))
    return set(rows)
