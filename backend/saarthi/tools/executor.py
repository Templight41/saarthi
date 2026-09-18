"""Action executor.

This is the only place a side effect happens, and it refuses to act on a
side-effecting tool without an explicit ALLOW. That refusal is what makes the
policy engine impossible to bypass rather than merely conventional.

Each attempt writes its own `actions` row, so a retry is visible in the audit
trail and the recovery-rate metric reads straight off the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import EventType, record_event
from ..database.database import utcnow
from ..database.enums import ActionStatus, EventStatus
from ..database.ids import next_id
from ..database.models import Action, Case
from ..schemas.agent import PolicyDecision, ProposedAction
from ..services.errors import EnterpriseAPIError
from .registry import ToolContext, ToolRegistry, ToolSpec


class PolicyViolation(RuntimeError):
    def __init__(self, tool: str, reason: str) -> None:
        super().__init__(f"Refusing to run side-effecting tool {tool}: {reason}")
        self.tool = tool
        self.reason = reason


@dataclass
class ActionFailure:
    action: Action
    error: Exception
    step: ProposedAction
    step_index: int
    kind: str = "TOOL"


class ActionFailed(RuntimeError):
    def __init__(self, failure: ActionFailure) -> None:
        super().__init__(str(failure.error))
        self.failure = failure


@dataclass
class ActionResult:
    action: Action
    output: dict[str, Any]


class ActionExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def attempt_number(self, session: AsyncSession, case_id: str, step_index: int) -> int:
        count = await session.scalar(
            select(func.count())
            .select_from(Action)
            .where(Action.case_id == case_id, Action.step_index == step_index)
        )
        return int(count or 0) + 1

    async def execute(
        self,
        ctx: ToolContext,
        step: ProposedAction,
        *,
        step_index: int,
        decision: PolicyDecision | None,
        attempt: int,
    ) -> ActionResult:
        session, case = ctx.session, ctx.case
        spec: ToolSpec = self.registry.get(step.tool)

        if spec.side_effecting and spec.requires_policy:
            if decision is None:
                raise PolicyViolation(step.tool, "no policy decision was recorded")
            if not decision.allowed:
                raise PolicyViolation(step.tool, f"policy returned {decision.decision.value}")

        args = spec.input_model.model_validate(step.args)

        # The key is derived here, never supplied by the model, so a retry of
        # the same step is guaranteed to carry the same key.
        idempotency_key = (
            spec.idempotency_key_fn(case, step.args) if spec.idempotency_key_fn else None
        )

        action = Action(
            id=await next_id(session, "action"),
            case_id=case.id,
            action_type=spec.name,
            status=ActionStatus.STARTED,
            input=step.args,
            attempt=attempt,
            step_index=step_index,
            idempotency_key=idempotency_key,
            policy_decision=decision.model_dump(mode="json") if decision else None,
        )
        session.add(action)
        await session.flush()

        await record_event(
            session,
            case,
            EventType.ACTION_RETRIED if attempt > 1 else EventType.ACTION_STARTED,
            message=(
                f"Retrying {spec.name} (attempt {attempt})" if attempt > 1 else f"Running {spec.name}"
            ),
            status=EventStatus.IN_PROGRESS,
            input=step.args,
            meta={"action_id": action.id, "attempt": attempt, "step_index": step_index},
        )

        try:
            output = await spec.handler(ctx, args)
        except EnterpriseAPIError as exc:
            action.status = ActionStatus.FAILED
            action.error = exc.as_dict()
            action.completed_at = utcnow()
            await record_event(
                session,
                case,
                EventType.ACTION_FAILED,
                message=f"{spec.name} failed: {exc.message}",
                status=EventStatus.FAILED,
                result=exc.as_dict(),
                meta={"action_id": action.id, "attempt": attempt},
            )
            raise ActionFailed(
                ActionFailure(action=action, error=exc, step=step, step_index=step_index)
            ) from exc

        action.status = ActionStatus.COMPLETED
        action.result = output
        action.completed_at = utcnow()
        if spec.side_effecting and case.first_action_at is None:
            case.first_action_at = utcnow()
        await session.flush()

        await record_event(
            session,
            case,
            EventType.ACTION_COMPLETED,
            message=f"{spec.name} completed",
            result=output,
            meta={"action_id": action.id, "attempt": attempt},
        )
        if spec.event_on_success is not None:
            await record_event(
                session,
                case,
                spec.event_on_success,
                message=_semantic_message(spec.name, output),
                result=output,
            )
        return ActionResult(action=action, output=output)


def _semantic_message(tool: str, output: dict) -> str:
    match tool:
        case "create_ticket":
            return f"Case logged as ticket {output.get('id')}"
        case "schedule_refund":
            return (
                f"Standby refund {output.get('id')} scheduled for ₹{output.get('amount')} "
                "if settlement does not complete"
            )
        case "draft_message":
            return "Merchant reply drafted"
        case "send_message":
            return "Merchant notified"
        case _:
            return f"{tool} completed"


async def completed_actions_for(session: AsyncSession, case: Case) -> list[str]:
    rows = await session.scalars(
        select(Action.action_type).where(
            Action.case_id == case.id, Action.status == ActionStatus.COMPLETED
        )
    )
    return sorted(set(rows))
