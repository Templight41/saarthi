"""Operational metrics.

The definition that matters: a case escalated *because policy mandated a human*
is correct behaviour, not an autonomy failure, so it is excluded from the
eligible denominator and counted in the escalation rate instead. Otherwise
Scenario C would make the system look worse the more correctly it behaves.

Human hours saved is an explicitly configurable demo estimate and is flagged
`simulated`, never presented as a measured result.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.database import utcnow
from ..database.enums import ActionStatus, CaseStatus, Resolution
from ..database.models import Action, AgentEvent, Case, Escalation, Message


@dataclass
class MetricValue:
    key: str
    label: str
    value: float | None
    unit: str
    description: str
    numerator: int | None = None
    denominator: int | None = None
    simulated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": self.value,
            "unit": self.unit,
            "description": self.description,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "simulated": self.simulated,
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


async def compute_metrics(session: AsyncSession, settings: Settings) -> dict:
    total_cases = int(await session.scalar(select(func.count()).select_from(Case)) or 0)

    terminal_rows = list(
        await session.scalars(
            select(Case).where(Case.status.in_([CaseStatus.RESOLVED, CaseStatus.ESCALATED]))
        )
    )
    # Correct, policy-required escalations are not autonomy failures.
    eligible = [c for c in terminal_rows if not c.human_required_by_policy]
    eligible_ids = {c.id for c in eligible}

    escalated_case_ids = set(
        await session.scalars(select(Escalation.case_id).distinct())
    )

    resolved = [c for c in terminal_rows if c.status == CaseStatus.RESOLVED]
    autonomously_resolved = [
        c
        for c in resolved
        if c.resolution == Resolution.AUTONOMOUS and c.id not in escalated_case_ids
    ]
    eligible_autonomous = [c for c in autonomously_resolved if c.id in eligible_ids]

    # --- Timings -----------------------------------------------------------
    first_action_deltas = [
        (c.first_action_at - c.created_at).total_seconds()
        for c in terminal_rows
        if c.first_action_at and c.created_at
    ]
    resolution_deltas = [
        (c.resolved_at - c.created_at).total_seconds()
        for c in resolved
        if c.resolved_at and c.created_at
    ]

    # --- Actions -----------------------------------------------------------
    action_rows = list(await session.scalars(select(Action)))
    verified = [a for a in action_rows if a.status == ActionStatus.COMPLETED]
    failed = [a for a in action_rows if a.status == ActionStatus.FAILED]

    recovered = 0
    for failure in failed:
        later_success = any(
            a.case_id == failure.case_id
            and a.action_type == failure.action_type
            and a.attempt > failure.attempt
            and a.status == ActionStatus.COMPLETED
            for a in action_rows
        )
        if later_success:
            recovered += 1

    # --- First contact resolution -----------------------------------------
    fcr = 0
    for case in eligible:
        if case.status != CaseStatus.RESOLVED or case.id in escalated_case_ids:
            continue
        inbound = int(
            await session.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.case_id == case.id, Message.direction == "INBOUND")
            )
            or 0
        )
        if inbound <= 1:
            fcr += 1

    # --- Audit coverage ----------------------------------------------------
    covered = 0
    for case in terminal_rows:
        types = set(
            await session.scalars(
                select(AgentEvent.event_type).where(AgentEvent.case_id == case.id)
            )
        )
        has_creation = "CASE_CREATED" in types
        has_terminal = bool({"CASE_RESOLVED", "CASE_ESCALATED"} & types)
        action_count = int(
            await session.scalar(
                select(func.count()).select_from(Action).where(Action.case_id == case.id)
            )
            or 0
        )
        started_count = int(
            await session.scalar(
                select(func.count())
                .select_from(AgentEvent)
                .where(
                    AgentEvent.case_id == case.id,
                    AgentEvent.event_type.in_(["ACTION_STARTED", "ACTION_RETRIED"]),
                )
            )
            or 0
        )
        if has_creation and has_terminal and started_count >= action_count:
            covered += 1

    human_approved = [c for c in resolved if c.resolution == Resolution.HUMAN_APPROVED]
    hours_saved = (
        len(autonomously_resolved) * settings.human_hours_saved_per_case
        + len(human_approved) * settings.human_hours_saved_per_approved_case
    )

    metrics = [
        MetricValue(
            "cases_handled",
            "Cases handled",
            float(total_cases),
            "count",
            "Every case Saarthi has taken ownership of.",
            numerator=total_cases,
        ),
        MetricValue(
            "autonomously_resolved",
            "Autonomously resolved",
            float(len(autonomously_resolved)),
            "count",
            "Resolved end to end with no human involvement.",
            numerator=len(autonomously_resolved),
        ),
        MetricValue(
            "human_escalations",
            "Human escalations",
            float(len(escalated_case_ids)),
            "count",
            "Cases handed to a person, with full context attached.",
            numerator=len(escalated_case_ids),
        ),
        MetricValue(
            "autonomous_resolution_rate",
            "Autonomous resolution rate",
            _ratio(len(eligible_autonomous), len(eligible)),
            "ratio",
            "Of the cases Saarthi was authorised to close, how many it closed alone. "
            "Cases where policy requires a human are excluded rather than counted as failures.",
            numerator=len(eligible_autonomous),
            denominator=len(eligible),
        ),
        MetricValue(
            "time_to_first_action_seconds",
            "Time to first meaningful action",
            round(sum(first_action_deltas) / len(first_action_deltas), 1)
            if first_action_deltas
            else None,
            "seconds",
            "From the merchant's message to the first controlled action taken.",
            denominator=len(first_action_deltas),
        ),
        MetricValue(
            "avg_resolution_seconds",
            "Average resolution time",
            round(sum(resolution_deltas) / len(resolution_deltas), 1)
            if resolution_deltas
            else None,
            "seconds",
            "From the merchant's message to a verified resolution.",
            denominator=len(resolution_deltas),
        ),
        MetricValue(
            "escalation_rate",
            "Escalation rate",
            _ratio(len(escalated_case_ids), total_cases),
            "ratio",
            "How often a person was needed.",
            numerator=len(escalated_case_ids),
            denominator=total_cases,
        ),
        MetricValue(
            "recovery_rate",
            "Recovery success rate",
            _ratio(recovered, len(failed)),
            "ratio",
            "Failed actions that Saarthi recovered from without a person.",
            numerator=recovered,
            denominator=len(failed),
        ),
        MetricValue(
            "first_contact_resolution",
            "First contact resolution",
            _ratio(fcr, len(eligible)),
            "ratio",
            "Closed on the merchant's first message, with no follow-up needed.",
            numerator=fcr,
            denominator=len(eligible),
        ),
        MetricValue(
            "action_success_rate",
            "Action success rate",
            _ratio(len(verified), len(verified) + len(failed)),
            "ratio",
            "Attempted actions that reached their intended state.",
            numerator=len(verified),
            denominator=len(verified) + len(failed),
        ),
        MetricValue(
            "actions_completed",
            "Actions completed",
            float(len(verified)),
            "count",
            "Controlled actions carried out and confirmed.",
            numerator=len(verified),
        ),
        MetricValue(
            "audit_coverage",
            "Audit coverage",
            _ratio(covered, len(terminal_rows)),
            "ratio",
            "Closed cases with a complete, machine-readable audit trail.",
            numerator=covered,
            denominator=len(terminal_rows),
        ),
        MetricValue(
            "human_hours_saved",
            "Human hours saved",
            round(hours_saved, 2),
            "hours",
            f"Demo estimate at {settings.human_hours_saved_per_case}h per autonomous case. "
            "This is a configured assumption, not a measured result.",
            simulated=True,
        ),
    ]

    return {
        "generated_at": utcnow().isoformat(),
        "metrics": [m.as_dict() for m in metrics],
        "note": "Computed over simulated enterprise data.",
    }
