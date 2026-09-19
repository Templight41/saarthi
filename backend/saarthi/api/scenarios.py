"""The Scenario Lab.

One endpoint per thing a person actually wants to do: see what there is, read
what one claims it will do, put the world back, run it, and check what
happened.

`status` is the interesting one. It does not report what the scenario said
would happen — it reads the case's audit trail and the case row, and ticks a
checkpoint only when there is an event or a status to back it. So a scenario
that quietly stopped working reports unticked checkpoints rather than a
confident summary, which is the same discipline verification applies to the
agent itself.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import event_types_for_case
from ..database.enums import CaseOrigin, CaseStatus, MessageChannel
from ..database.ids import next_id
from ..database.models import Case
from ..database.seed import seed_all
from ..memory.knowledge import seed_knowledge
from ..runtime import SaarthiRuntime
from ..services import ops_service
from ..simulation.failure_injection import simulation_state
from ..simulation.scenarios import SCENARIOS, Scenario, ScenarioTrigger, resolve
from ..workflows import steps
from .deps import get_runtime, get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


def _require(name: str) -> Scenario:
    scenario = resolve(name)
    if scenario is None:
        known = ", ".join(SCENARIOS)
        raise HTTPException(404, f"Unknown scenario {name}. Known scenarios: {known}")
    return scenario


async def _restore(session: AsyncSession, runtime: SaarthiRuntime, scenario: Scenario) -> None:
    """Deterministic ground state, then arm the one thing this scenario changes."""
    await runtime.runner.shutdown()
    if runtime.workflows is not None:
        await runtime.workflows.shutdown()
    simulation_state.reset()

    await seed_all(session)
    await seed_knowledge(session, runtime.memory)
    await scenario.arm(simulation_state, session)
    simulation_state.active_scenario = scenario.id
    await session.commit()


@router.get("")
async def list_scenarios() -> dict:
    return {
        "scenarios": [s.as_dict() for s in SCENARIOS.values()],
        "active": simulation_state.active_scenario,
    }


@router.get("/{name}")
async def get_scenario(name: str) -> dict:
    return _require(name).as_dict()


@router.post("/{name}/reset")
async def reset_scenario(
    name: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    """Fixtures back to ground state and the scenario armed, but not started."""
    scenario = _require(name)
    await _restore(session, runtime, scenario)
    return {"scenario": scenario.id, "armed": True, "case_id": None}


@router.post("/{name}/run", status_code=202)
async def run_scenario(
    name: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    """Reset, arm, and start it the way it would really start.

    A merchant-triggered scenario opens a case from the merchant's own words.
    A monitor-triggered one opens nothing: the settlement is simply overdue,
    and the monitor is left to reach its own conclusion. Returning a case id
    for the proactive scenario would be a small lie about who noticed.
    """
    scenario = _require(name)
    await _restore(session, runtime, scenario)

    if scenario.trigger is ScenarioTrigger.MONITOR:
        run = await runtime.workflows.run_now(session, "settlement_monitor")
        await session.commit()
        # The monitor runs out of process. Wait for the observable result
        # rather than handing back a case id that may not exist yet: a
        # one-click demo that needs a second click is not one click.
        drain = getattr(runtime.workflows, "drain", None)
        if drain is not None:
            await drain()
        case_id = await _await_proactive_case(session, scenario.transaction_id)

        fallback = None
        if case_id is None:
            # n8n answered its webhook but never called back — a stale imported
            # copy does this, and it is reachable so no engine-level fallback
            # fires. Run the same step function in process so the scenario is
            # still demonstrable, and say plainly that that is what happened.
            logger.warning(
                "The workflow engine did not open a proactive case; scanning in process"
            )
            await steps.settlement_scan(
                session, run.id, runtime.settings, runner=runtime.runner
            )
            await session.commit()
            case_id = await _proactive_case_for(session, scenario.transaction_id)
            fallback = "LOCAL_SCAN"

        return {
            "scenario": scenario.id,
            "trigger": scenario.trigger.value,
            "workflow_run_id": run.id,
            "case_id": case_id,
            "engine": getattr(runtime.workflows, "name", "none"),
            # Null when the engine did its job. Named when it did not, because
            # a demo that silently repairs itself teaches the wrong thing.
            "fallback": fallback,
        }

    case = Case(
        id=await next_id(session, "case"),
        merchant_id=scenario.merchant_id,
        transaction_id=scenario.transaction_id,
        status=CaseStatus.RECEIVED,
        origin=CaseOrigin.MERCHANT_CHAT,
        original_message=scenario.message or "",
        scenario=scenario.id,
    )
    session.add(case)
    await session.flush()

    from ..agent.events import EventType, record_event
    from ..database.enums import Actor

    await record_event(
        session, case, EventType.CASE_CREATED, actor=Actor.MERCHANT, message="New merchant case received"
    )
    await ops_service.record_inbound_message(
        session, case_id=case.id, content=case.original_message, channel=MessageChannel.CHAT
    )
    await record_event(
        session,
        case,
        EventType.MESSAGE_RECEIVED,
        actor=Actor.MERCHANT,
        message=case.original_message,
        meta={"channel": MessageChannel.CHAT.value},
    )
    await session.commit()

    await runtime.runner.start(case.id)
    return {
        "scenario": scenario.id,
        "trigger": scenario.trigger.value,
        "case_id": case.id,
        "merchant_id": scenario.merchant_id,
        "message": scenario.message,
    }


@router.get("/{name}/status")
async def scenario_status(
    name: str,
    case_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """What actually happened, read back off the audit trail."""
    scenario = _require(name)

    case = None
    if case_id:
        case = await session.get(Case, case_id)
    else:
        case = await session.scalar(
            select(Case)
            .where(Case.scenario == scenario.id)
            .order_by(Case.created_at.desc())
            .limit(1)
        )
        if case is None and scenario.transaction_id:
            case = await session.scalar(
                select(Case)
                .where(Case.transaction_id == scenario.transaction_id)
                .order_by(Case.created_at.desc())
                .limit(1)
            )

    if case is None:
        return {
            "scenario": scenario.id,
            "armed": simulation_state.active_scenario == scenario.id,
            "case_id": None,
            "phase": "NOT_STARTED",
            "checkpoints": [
                {"label": c.label, "reached": False} for c in scenario.checkpoints
            ],
        }

    seen = await event_types_for_case(session, case.id)
    statuses = _statuses_reached(case)
    checkpoints = [
        {
            "label": c.label,
            "reached": (c.event_type in seen if c.event_type else False)
            or (c.case_status in statuses if c.case_status else False),
        }
        for c in scenario.checkpoints
    ]

    return {
        "scenario": scenario.id,
        "armed": simulation_state.active_scenario == scenario.id,
        "case_id": case.id,
        "case_status": case.status.value,
        "origin": case.origin.value,
        "owner": case.owner.value,
        "resolution": case.resolution.value if case.resolution else None,
        "requires_human": case.requires_human,
        "human_required_by_policy": case.human_required_by_policy,
        "wait_reason": case.wait_reason,
        "phase": _phase(case),
        "checkpoints": checkpoints,
        "reached": sum(1 for c in checkpoints if c["reached"]),
        "total": len(checkpoints),
    }


def _statuses_reached(case: Case) -> set[str]:
    """Statuses this case has been in, including the one it is in now.

    A parked case's current status is not its final one, so a checkpoint on
    ESCALATED must still tick after a human resolves it.
    """
    reached = {case.status.value}
    if case.resolution is not None:
        reached.add(CaseStatus.RESOLVED.value)
    if case.requires_human or case.human_required_by_policy:
        reached.add(CaseStatus.ESCALATED.value)
    return reached


def _phase(case: Case) -> str:
    if case.status == CaseStatus.RESOLVED:
        return "RESOLVED"
    if case.status == CaseStatus.ESCALATED:
        return "AWAITING_HUMAN"
    if case.wait_reason:
        return "WAITING"
    return "RUNNING"


async def _await_proactive_case(
    session: AsyncSession, transaction_id: str | None, *, timeout: float = 6.0
) -> str | None:
    """Give an out-of-process engine a moment to call back."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        case_id = await _proactive_case_for(session, transaction_id)
        if case_id or asyncio.get_running_loop().time() >= deadline:
            return case_id
        await asyncio.sleep(0.5)
        # No expire_all() here: it would make the next attribute access lazy-load
        # outside the greenlet context. Each poll is a fresh SELECT, and under
        # READ COMMITTED that already sees what another process has committed.


async def _proactive_case_for(session: AsyncSession, transaction_id: str | None) -> str | None:
    if not transaction_id:
        return None
    return await session.scalar(
        select(Case.id)
        .where(Case.transaction_id == transaction_id, Case.origin == CaseOrigin.PROACTIVE)
        .order_by(Case.created_at.desc())
        .limit(1)
    )
