"""The Saarthi supervisor.

One handler per state, each opening its own session and committing before the
next runs, so the dashboard can watch the agent think in real time. Handlers
read through tools and write side effects only through the executor; the only
direct writes are to the case row and its audit trail.

A handler returning `None` for the next state parks the case. That is how a
Scenario A case waits for settlement without burning a task.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..database.database import utcnow
from ..database.enums import (
    CaseOwner,
    CaseStatus,
    EscalationStatus,
    EventStatus,
    Resolution,
)
from ..database.models import Case
from ..policy.rules import PolicyContext
from ..schemas.agent import (
    Diagnosis,
    EscalationReason,
    Plan,
    PolicyDecision,
    PolicyDecisionType,
    ProposedAction,
    RecoveryDecision,
    VerificationStatus,
)
from ..services import ledger_service, refund_service
from ..services.errors import EnterpriseAPIError
from ..tools.executor import ActionFailed, ActionFailure, PolicyViolation
from ..tools.registry import ToolContext, UnknownTool
from ..verification.verifier import (
    CONDITION_SETTLEMENT_COMPLETED,
)
from .context import CaseContext, build_context
from .events import EventType, record_event
from .planner import REPLAN_REFUND_CONDITION_MET, REPLAN_SETTLEMENT_COMPLETED
from .state_machine import transition

logger = logging.getLogger(__name__)

WAIT_SETTLEMENT = "SETTLEMENT_PENDING"
WAIT_HUMAN = "AWAITING_HUMAN"


@dataclass
class CaseRunContext:
    case_id: str
    trigger: str = "NEW_MESSAGE"
    context: CaseContext | None = None
    diagnosis: Diagnosis | None = None
    candidate: Plan | None = None
    decisions: dict[int, PolicyDecision] = field(default_factory=dict)
    plan: Plan | None = None
    outputs: dict[int, dict] = field(default_factory=dict)
    failure: ActionFailure | None = None
    replan_reason: str | None = None
    escalation_reason: EscalationReason | None = None


@dataclass
class StepOutcome:
    next_state: CaseStatus | None
    reason: str = ""


class Supervisor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings,
        llm,
        memory,
        registry,
        executor,
        policy,
        planner,
        diagnoser,
        verifier,
        recovery,
        escalation,
        simulation,
        workflows=None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.llm = llm
        self.memory = memory
        self.registry = registry
        self.executor = executor
        self.policy = policy
        self.planner = planner
        self.diagnoser = diagnoser
        self.verifier = verifier
        self.recovery = recovery
        self.escalation = escalation
        self.simulation = simulation
        self.workflows = workflows

        self.handlers = {
            CaseStatus.RECEIVED: self._on_received,
            CaseStatus.IDENTIFYING: self._on_identifying,
            CaseStatus.INVESTIGATING: self._on_investigating,
            CaseStatus.DIAGNOSING: self._on_diagnosing,
            CaseStatus.POLICY_CHECK: self._on_policy_check,
            CaseStatus.PLANNING: self._on_planning,
            CaseStatus.ACTING: self._on_acting,
            CaseStatus.VERIFYING: self._on_verifying,
            CaseStatus.RECOVERING: self._on_recovering,
            CaseStatus.ESCALATED: self._on_escalated,
        }

    # ------------------------------------------------------------------
    async def run_case(self, case_id: str, *, trigger: str = "NEW_MESSAGE") -> None:
        await self._loop(CaseRunContext(case_id=case_id, trigger=trigger))

    async def resume_case(self, case_id: str, *, trigger: str) -> None:
        async with self.session_factory() as session:
            case = await session.get(Case, case_id)
            if case is None:
                return
            await record_event(
                session,
                case,
                EventType.CASE_RESUMED,
                message=f"Case resumed: {trigger.replace('_', ' ').lower()}",
                meta={"trigger": trigger},
            )
            case.wait_reason = None
            await session.commit()
        await self._loop(CaseRunContext(case_id=case_id, trigger=trigger))

    def _delay(self) -> float:
        override = self.simulation.step_delay_seconds
        return override if override is not None else self.settings.agent_step_delay_seconds

    async def _loop(self, ctx: CaseRunContext) -> None:
        for _ in range(self.settings.agent_max_steps):
            async with self.session_factory() as session:
                case = await session.get(Case, ctx.case_id)
                if case is None:
                    return
                if case.owner == CaseOwner.HUMAN:
                    logger.info("Case %s is owned by a human; agent standing down", case.id)
                    return
                if case.status == CaseStatus.RESOLVED:
                    return

                handler = self.handlers[case.status]
                try:
                    outcome = await handler(session, case, ctx)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Supervisor handler failed for %s", case.id)
                    await record_event(
                        session,
                        case,
                        EventType.AGENT_ERROR,
                        message=f"Agent error in {case.status.value}: {exc}",
                        status=EventStatus.FAILED,
                    )
                    await session.commit()
                    return

                if outcome.next_state is not None:
                    await transition(session, case, outcome.next_state, outcome.reason)
                await session.commit()

            if outcome.next_state is None:
                return
            delay = self._delay()
            if delay:
                await asyncio.sleep(delay)

        logger.warning("Case %s hit the step cap", ctx.case_id)

    # ------------------------------------------------------------------
    async def _on_received(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        return StepOutcome(CaseStatus.IDENTIFYING, "identifying merchant and transaction")

    async def _on_identifying(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        merchant = await ledger_service.get_merchant(session, case.merchant_id)
        await record_event(
            session,
            case,
            EventType.MERCHANT_IDENTIFIED,
            message=(
                f"{merchant.name} ({merchant.id}), risk {merchant.risk_level.value}, "
                f"autonomous refund limit ₹{Decimal(merchant.autonomous_refund_limit):,.0f}"
            ),
            result={
                "merchant_id": merchant.id,
                "name": merchant.name,
                "risk_level": merchant.risk_level.value,
                "autonomous_refund_limit": str(merchant.autonomous_refund_limit),
            },
        )

        from .identify import resolve_transaction

        resolved, how, candidates = await resolve_transaction(
            session, case.merchant_id, case.original_message, hint=case.transaction_id
        )
        if resolved is None:
            ctx.escalation_reason = EscalationReason.IDENTIFICATION_FAILED
            await record_event(
                session,
                case,
                EventType.TRANSACTION_IDENTIFIED,
                message="Could not identify a single transaction from the merchant's message",
                status=EventStatus.WARNING,
                result={"candidates": candidates},
            )
            return StepOutcome(CaseStatus.ESCALATED, "no single transaction could be identified")

        case.transaction_id = resolved
        await record_event(
            session,
            case,
            EventType.TRANSACTION_IDENTIFIED,
            message=f"Transaction {resolved} matched",
            result={"transaction_id": resolved, "how": how},
        )
        return StepOutcome(CaseStatus.INVESTIGATING, "retrieving current business state")

    async def _on_investigating(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        try:
            txn = await ledger_service.get_transaction(session, case.transaction_id)
        except EnterpriseAPIError:
            ctx.escalation_reason = EscalationReason.CONTEXT_UNAVAILABLE
            return StepOutcome(CaseStatus.ESCALATED, "transaction could not be retrieved")

        await record_event(
            session,
            case,
            EventType.TRANSACTION_RETRIEVED,
            message=(
                f"{txn.id}: ₹{Decimal(txn.amount):,.0f}, payment {txn.payment_status.value}, "
                f"customer debited {'yes' if txn.customer_debited else 'no'}"
            ),
            result={
                "amount": str(txn.amount),
                "payment_status": txn.payment_status.value,
                "customer_debited": txn.customer_debited,
            },
        )

        try:
            eta = await ledger_service.get_settlement_eta(session, case.transaction_id)
            await record_event(
                session,
                case,
                EventType.SETTLEMENT_CHECKED,
                message=(
                    f"Settlement {eta.status.value}"
                    + (f", {_eta_phrase(eta.eta_seconds)}" if eta.eta_seconds is not None else "")
                    + (f" ({eta.delay_reason})" if eta.delay_reason else "")
                ),
                result={
                    "status": eta.status.value,
                    "eta_seconds": eta.eta_seconds,
                    "overdue": eta.overdue,
                    "delay_reason": eta.delay_reason,
                },
            )
        except EnterpriseAPIError:
            pass

        disputes = await ledger_service.get_disputes(session, case.transaction_id)
        await record_event(
            session,
            case,
            EventType.DISPUTE_CHECKED,
            message=(
                f"{len(disputes)} dispute(s) on this transaction" if disputes else "No disputes on file"
            ),
            result={
                "count": len(disputes),
                "disputes": [
                    {"id": d.id, "type": d.type.value, "status": d.status.value} for d in disputes
                ],
            },
        )

        memory_result = await self._search_memory(session, case)
        ctx.context = await build_context(session, case, memory=memory_result)
        await self._record_patterns(session, case, ctx.context)
        return StepOutcome(CaseStatus.DIAGNOSING, "reasoning about the root cause")

    async def _record_patterns(self, session, case: Case, context: CaseContext) -> None:
        """Put what keeps happening to this merchant into the audit trail.

        One event per case, not one per pattern: this is a single observation
        about the merchant, and the payload carries the counts a reader would
        otherwise have to take on trust. It changes nothing on its own — the
        policy engine never sees it.
        """
        if not context.patterns:
            return
        headline = context.patterns[0]
        await record_event(
            session,
            case,
            EventType.PATTERN_DETECTED,
            message=(
                f"{headline['summary']} — {len(context.patterns)} recurring pattern(s) "
                f"for {case.merchant_id}"
                if len(context.patterns) > 1
                else f"{headline['summary']} for {case.merchant_id}"
            ),
            status=EventStatus.INFO,
            result={"patterns": context.patterns},
        )

    async def _search_memory(self, session, case: Case) -> dict | None:
        if self.memory is None:
            return None
        try:
            result = await asyncio.wait_for(
                self.memory.search(
                    session,
                    text=case.original_message,
                    merchant_id=case.merchant_id,
                    transaction_id=case.transaction_id,
                    exclude_case_id=case.id,
                ),
                # Slightly longer than the inner embed budget so the inner
                # failure surfaces with its real cause.
                timeout=self.settings.memory_timeout_seconds + 5,
            )
        except Exception as exc:  # noqa: BLE001 - memory must never block a case
            logger.warning("Memory search failed (%s); continuing without it", exc)
            return None

        similar = result.get("similar_cases", [])
        await record_event(
            session,
            case,
            EventType.MEMORY_RETRIEVED,
            message=(
                f"{len(similar)} similar historical case(s) found"
                if similar
                else "No similar historical cases found"
            ),
            result={
                "count": len(similar),
                "provider": result.get("provider"),
                "similar_cases": [
                    {
                        "case_id": hit.get("case_id"),
                        "diagnosis": hit.get("diagnosis"),
                        "resolution": hit.get("resolution"),
                        "similarity": hit.get("similarity"),
                    }
                    for hit in similar
                ],
                "merchant_history": result.get("merchant_history"),
            },
        )
        return result

    async def _on_diagnosing(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        if ctx.context is None:
            ctx.context = await build_context(session, case)

        diagnosis = await self.diagnoser.diagnose(case.original_message, ctx.context)
        ctx.diagnosis = diagnosis

        fallback_reason = getattr(self.llm, "last_fallback_reason", None)
        if fallback_reason:
            await record_event(
                session,
                case,
                EventType.LLM_FALLBACK,
                message="Primary model unavailable; used the deterministic fallback",
                status=EventStatus.WARNING,
                meta={"reason": fallback_reason},
            )

        case.intent = diagnosis.intent.value
        case.diagnosis = diagnosis.model_dump(mode="json")
        case.confidence = diagnosis.confidence
        case.risk = diagnosis.risk
        case.requires_human = diagnosis.requires_human

        await record_event(
            session,
            case,
            EventType.DIAGNOSIS_COMPLETE,
            message=f"Issue classified: {_humanise(diagnosis.root_cause.value)}",
            result=diagnosis.model_dump(mode="json"),
            meta={
                "clamped_fields": diagnosis.clamped_fields,
                "llm_suggested_actions": diagnosis.suggested_actions,
            },
        )

        if diagnosis.merchant_requests_human:
            ctx.escalation_reason = EscalationReason.MERCHANT_REQUESTED_HUMAN
            return StepOutcome(CaseStatus.ESCALATED, "the merchant asked to speak to a person")

        if diagnosis.confidence < self.settings.diagnosis_confidence_threshold:
            ctx.escalation_reason = EscalationReason.LOW_CONFIDENCE
            return StepOutcome(
                CaseStatus.ESCALATED,
                f"confidence {diagnosis.confidence:.2f} is below the "
                f"{self.settings.diagnosis_confidence_threshold:.2f} threshold",
            )

        return StepOutcome(CaseStatus.POLICY_CHECK, "checking what is authorised")

    async def _on_policy_check(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        if ctx.context is None:
            ctx.context = await build_context(session, case)
        if ctx.diagnosis is None and case.diagnosis:
            ctx.diagnosis = Diagnosis.model_validate(case.diagnosis)

        candidate = self.planner.candidate_plan(
            ctx.diagnosis, ctx.context, replan_reason=ctx.replan_reason
        )
        ctx.candidate = candidate
        ctx.replan_reason = None

        if not candidate.steps:
            ctx.escalation_reason = EscalationReason.NO_PERMITTED_PLAN
            return StepOutcome(CaseStatus.ESCALATED, "no applicable action was identified")

        policy_ctx = await self._policy_context(session, case, ctx)
        decisions: dict[int, PolicyDecision] = {}
        recorded: list[dict] = []
        side_effecting_seen = False

        for index, step in enumerate(candidate.steps):
            spec = self.registry.get(step.tool)
            if not (spec.side_effecting and spec.requires_policy):
                continue
            side_effecting_seen = True
            decision = await self.policy.evaluate(session, step, policy_ctx)
            decisions[index] = decision
            recorded.append(
                {
                    "action": step.tool,
                    "decision": decision.decision.value,
                    "policy_ids": decision.policy_ids,
                    "reasons": decision.reasons,
                }
            )
            await record_event(
                session,
                case,
                EventType.POLICY_CHECKED,
                message=f"{step.tool}: {decision.decision.value} — {'; '.join(decision.reasons)}",
                status=(
                    EventStatus.SUCCESS
                    if decision.decision == PolicyDecisionType.ALLOW
                    else EventStatus.WARNING
                ),
                result={
                    "decision": decision.decision.value,
                    "policy_ids": decision.policy_ids,
                    "reasons": decision.reasons,
                    "action": step.tool,
                    "amount": step.args.get("amount"),
                },
            )

        if not side_effecting_seen:
            await record_event(
                session,
                case,
                EventType.POLICY_CHECKED,
                message="No side-effecting action proposed; nothing to authorise",
                result={"decision": "ALLOW", "policy_ids": [], "reasons": []},
            )

        ctx.decisions = decisions
        case.policy_decisions = recorded

        primary = candidate.primary_step
        if primary is not None:
            primary_decision = decisions.get(primary)
            if (
                primary_decision is not None
                and primary_decision.decision == PolicyDecisionType.REQUIRES_APPROVAL
            ):
                ctx.escalation_reason = self.escalation.derive_reason(
                    ctx.diagnosis, primary_decision
                )
                return StepOutcome(CaseStatus.ESCALATED, "the required action needs human approval")

        return StepOutcome(CaseStatus.PLANNING, "assembling the permitted plan")

    async def _on_planning(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        plan = self.planner.finalize(ctx.candidate, ctx.decisions, ctx.context)
        ctx.plan = plan
        case.plan = plan.model_dump(mode="json")
        case.plan_cursor = 0
        ctx.outputs = {}

        if not plan.steps:
            ctx.escalation_reason = EscalationReason.NO_PERMITTED_PLAN
            return StepOutcome(CaseStatus.ESCALATED, "every proposed action was denied by policy")

        await record_event(
            session,
            case,
            EventType.PLAN_CREATED,
            message=f"Plan: {', '.join(s.tool for s in plan.steps)}",
            result={
                "goal": plan.goal,
                "steps": [{"tool": s.tool, "purpose": s.purpose} for s in plan.steps],
                "goal_conditions": [c.kind for c in plan.goal_conditions],
            },
        )
        return StepOutcome(CaseStatus.ACTING, "executing the permitted plan")

    async def _on_acting(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        plan = ctx.plan or (Plan.model_validate(case.plan) if case.plan else None)
        if plan is None:
            ctx.escalation_reason = EscalationReason.NO_PERMITTED_PLAN
            return StepOutcome(CaseStatus.ESCALATED, "no plan to execute")
        ctx.plan = plan

        tool_ctx = ToolContext(
            session=session, case=case, simulation=self.simulation, runtime=self
        )

        while case.plan_cursor < len(plan.steps):
            index = case.plan_cursor
            step = plan.steps[index]

            # A send_message step takes the id drafted by the preceding step.
            if step.tool == "send_message" and not step.args.get("message_id"):
                drafted = ctx.outputs.get(index - 1, {})
                if drafted.get("id"):
                    step.args = {**step.args, "message_id": drafted["id"]}

            attempt = await self.executor.attempt_number(session, case.id, index)
            case.current_action = {
                "type": step.tool,
                "status": "IN_PROGRESS",
                "attempt": attempt,
            }
            try:
                result = await self.executor.execute(
                    tool_ctx,
                    step,
                    step_index=index,
                    decision=ctx.decisions.get(index),
                    attempt=attempt,
                )
            except ActionFailed as failed:
                ctx.failure = failed.failure
                case.current_action = {"type": step.tool, "status": "FAILED", "attempt": attempt}
                return StepOutcome(CaseStatus.RECOVERING, f"{step.tool} failed")
            except (PolicyViolation, UnknownTool) as exc:
                ctx.escalation_reason = EscalationReason.NO_PERMITTED_PLAN
                await record_event(
                    session,
                    case,
                    EventType.ACTION_FAILED,
                    message=str(exc),
                    status=EventStatus.FAILED,
                )
                return StepOutcome(CaseStatus.ESCALATED, "an action was refused before execution")

            ctx.outputs[index] = {**result.output, "_tool": step.tool}
            case.plan_cursor = index + 1

        case.current_action = {"type": "VERIFICATION", "status": "IN_PROGRESS", "attempt": 1}
        return StepOutcome(CaseStatus.VERIFYING, "verifying the resulting business state")

    async def _on_verifying(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        plan = ctx.plan or (Plan.model_validate(case.plan) if case.plan else None)
        if plan is None:
            return StepOutcome(CaseStatus.RECOVERING, "nothing to verify")

        result = await self.verifier.verify_plan(session, case, plan, outputs=ctx.outputs)

        for check in result.checks:
            event_type = (
                EventType.SETTLEMENT_VERIFIED if check.kind == "settlement" else EventType.ACTION_VERIFIED
            )
            await record_event(
                session,
                case,
                event_type,
                message=check.message,
                status=(
                    EventStatus.SUCCESS
                    if check.status == VerificationStatus.VERIFIED
                    else EventStatus.INFO
                ),
                result={"observed": check.observed, "expected": check.expected},
            )

        if result.status == VerificationStatus.VERIFIED:
            case.resolution = (
                Resolution.HUMAN_APPROVED if case.human_override else Resolution.AUTONOMOUS
            )
            if self.workflows is not None:
                await self._start_memory_ingestion(session, case)
            return StepOutcome(CaseStatus.RESOLVED, "business state verified")

        if result.status == VerificationStatus.CONDITION_TRIGGERED:
            ctx.replan_reason = (
                REPLAN_SETTLEMENT_COMPLETED
                if result.condition == CONDITION_SETTLEMENT_COMPLETED
                else REPLAN_REFUND_CONDITION_MET
            )
            ctx.context = await build_context(session, case)
            reason = (
                "settlement completed, standing down the refund"
                if result.condition == CONDITION_SETTLEMENT_COMPLETED
                else "refund condition met, issuing the refund"
            )
            return StepOutcome(CaseStatus.POLICY_CHECK, reason)

        if result.status == VerificationStatus.PENDING:
            case.wait_reason = WAIT_SETTLEMENT
            scheduled = await refund_service.find_scheduled_refund_for_case(session, case.id)
            case.current_action = {
                "type": "SCHEDULED_REFUND_WAIT",
                "status": "WAITING",
                "attempt": 1,
                "next_check_at": (
                    scheduled.scheduled_for.isoformat()
                    if scheduled and scheduled.scheduled_for
                    else None
                ),
            }
            await record_event(
                session,
                case,
                EventType.VERIFICATION_PENDING,
                message="Waiting for settlement; the standby refund stays armed",
                status=EventStatus.INFO,
                result={"wait_reason": WAIT_SETTLEMENT},
            )
            if self.workflows is not None:
                await self._start_scheduled_refund(session, case)
            return StepOutcome(None, "parked awaiting settlement")

        # FAILED
        ctx.failure = ActionFailure(
            action=_synthetic_action(case),
            error=RuntimeError(
                "; ".join(c.message for c in result.checks if c.status == VerificationStatus.FAILED)
            ),
            step=ProposedAction(tool="verification", args={}),
            step_index=case.plan_cursor,
            kind="VERIFICATION",
        )
        return StepOutcome(CaseStatus.RECOVERING, "verification did not confirm the intended state")

    async def _on_recovering(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        failure = ctx.failure
        if failure is None:
            ctx.escalation_reason = EscalationReason.RECOVERY_EXHAUSTED
            return StepOutcome(CaseStatus.ESCALATED, "recovery had nothing to work from")

        await record_event(
            session,
            case,
            EventType.RECOVERY_STARTED,
            message=f"Recovery initiated for {failure.step.tool} (attempt {failure.action.attempt})",
            status=EventStatus.IN_PROGRESS,
        )

        policy_ctx = await self._policy_context(session, case, ctx)
        plan = await self.recovery.recover(
            session,
            failure,
            policy_ctx=policy_ctx,
            planner=self.planner,
            case_ctx=ctx.context,
        )

        await record_event(
            session,
            case,
            EventType.FAILURE_CLASSIFIED,
            message=f"Failure classified as {_humanise(plan.failure_class.value)}",
            result={"failure_class": plan.failure_class.value},
        )
        await record_event(
            session,
            case,
            (
                EventType.REFUND_STATE_CHECKED
                if failure.step.tool in {"issue_refund", "schedule_refund"}
                else EventType.SIDE_EFFECT_CHECKED
            ),
            message=plan.side_effect.message,
            result={"status": plan.side_effect.status.value, "evidence": plan.side_effect.evidence},
        )
        await record_event(
            session,
            case,
            EventType.RECOVERY_DECIDED,
            message=f"{_humanise(plan.decision.value)}: {plan.reason}",
            result={"decision": plan.decision.value, "reason": plan.reason},
        )

        match plan.decision:
            case RecoveryDecision.RETRY:
                return StepOutcome(CaseStatus.ACTING, "retrying safely with the same idempotency key")
            case RecoveryDecision.SIDE_EFFECT_ALREADY_APPLIED:
                return StepOutcome(CaseStatus.VERIFYING, "the operation already landed; verifying")
            case RecoveryDecision.ALTERNATIVE:
                current = ctx.plan or Plan.model_validate(case.plan)
                steps = list(current.steps)
                if plan.alternative is not None and case.plan_cursor < len(steps):
                    steps[case.plan_cursor] = plan.alternative
                ctx.plan = Plan(
                    goal=current.goal,
                    steps=steps,
                    goal_conditions=current.goal_conditions,
                    primary_step=current.primary_step,
                    trigger=current.trigger,
                )
                case.plan = ctx.plan.model_dump(mode="json")
                return StepOutcome(CaseStatus.ACTING, "using a permitted alternative action")
            case _:
                ctx.escalation_reason = EscalationReason(
                    plan.escalation_reason or EscalationReason.RECOVERY_EXHAUSTED.value
                )
                return StepOutcome(CaseStatus.ESCALATED, plan.reason)

    async def _on_escalated(self, session, case: Case, ctx: CaseRunContext) -> StepOutcome:
        existing = await self.escalation.pending_for_case(session, case.id)

        if ctx.trigger == "HUMAN_APPROVED":
            return await self._resume_after_approval(session, case, ctx)
        if ctx.trigger == "HUMAN_REJECTED":
            return await self._resume_after_rejection(session, case, ctx)

        if existing is not None:
            case.wait_reason = WAIT_HUMAN
            return StepOutcome(None, "awaiting a human decision")

        reason = ctx.escalation_reason or EscalationReason.NO_PERMITTED_PLAN
        if ctx.diagnosis is None and case.diagnosis:
            ctx.diagnosis = Diagnosis.model_validate(case.diagnosis)
        if ctx.context is None:
            ctx.context = await build_context(session, case)

        primary_step = None
        primary_decision = None
        if ctx.candidate is not None and ctx.candidate.primary_step is not None:
            primary_step = ctx.candidate.steps[ctx.candidate.primary_step]
            primary_decision = ctx.decisions.get(ctx.candidate.primary_step)

        amount = None
        if primary_step is not None and primary_step.args.get("amount"):
            amount = Decimal(str(primary_step.args["amount"]))
        elif ctx.diagnosis is not None and ctx.diagnosis.requested_amount is not None:
            amount = ctx.diagnosis.requested_amount

        escalation = await self.escalation.create(
            session,
            case,
            reason=reason,
            diagnosis=ctx.diagnosis,
            decision=primary_decision,
            pending_action=primary_step,
            context_snapshot={
                "merchant": ctx.context.merchant,
                "transaction": ctx.context.transaction,
                "settlement": ctx.context.settlement,
                "disputes": ctx.context.disputes,
                "diagnosis_summary": ctx.diagnosis.summary if ctx.diagnosis else "",
            },
            amount=amount,
        )

        await self._notify(
            session,
            case,
            ctx,
            stage="ESCALATED",
            facts={
                "transaction_id": case.transaction_id,
                "amount": str(amount) if amount else None,
                "reason_text": _humanise(reason.value),
            },
        )
        if self.workflows is not None:
            await self._start_human_approval(session, case, escalation)

        case.wait_reason = WAIT_HUMAN
        return StepOutcome(None, "awaiting a human decision")

    async def _resume_after_approval(
        self, session, case: Case, ctx: CaseRunContext
    ) -> StepOutcome:
        escalation = await self.escalation.latest_for_case(session, case.id)
        if escalation is None or not escalation.pending_action:
            return StepOutcome(None, "nothing pending to execute")

        step = ProposedAction.model_validate(escalation.pending_action)
        if ctx.context is None:
            ctx.context = await build_context(session, case)
        if ctx.diagnosis is None and case.diagnosis:
            ctx.diagnosis = Diagnosis.model_validate(case.diagnosis)

        policy_ctx = await self._policy_context(session, case, ctx)
        decision = await self.policy.evaluate(session, step, policy_ctx)

        await record_event(
            session,
            case,
            EventType.POLICY_CHECKED,
            message=f"{step.tool}: {decision.decision.value} — {'; '.join(decision.reasons)}",
            status=(
                EventStatus.SUCCESS
                if decision.decision == PolicyDecisionType.ALLOW
                else EventStatus.WARNING
            ),
            result={
                "decision": decision.decision.value,
                "policy_ids": decision.policy_ids,
                "reasons": decision.reasons,
                "action": step.tool,
            },
        )

        if decision.decision != PolicyDecisionType.ALLOW:
            # Approval authorises a judgement call, not an impossible operation.
            ctx.escalation_reason = EscalationReason.STATE_CONFLICT
            escalation.status = EscalationStatus.APPROVED
            await session.flush()
            await self.escalation.create(
                session,
                case,
                reason=EscalationReason.STATE_CONFLICT,
                diagnosis=ctx.diagnosis,
                decision=decision,
                pending_action=step,
                context_snapshot={"note": "Approved action is still blocked by transaction state"},
                amount=Decimal(str(step.args.get("amount"))) if step.args.get("amount") else None,
            )
            case.wait_reason = WAIT_HUMAN
            return StepOutcome(None, "approved action is still blocked by current state")

        steps = [step]
        goal_conditions = []
        from ..schemas.agent import GoalCondition

        if step.tool == "issue_refund":
            goal_conditions.append(
                GoalCondition(
                    kind="REFUND_COMPLETED", params={"transaction_id": case.transaction_id}
                )
            )
        if ctx.context.disputes:
            steps.append(
                ProposedAction(
                    tool="update_dispute",
                    args={"dispute_id": ctx.context.disputes[0]["id"], "status": "RESOLVED"},
                    purpose="Close the dispute after the approved refund",
                )
            )
        steps += [
            ProposedAction(
                tool="draft_message",
                args={
                    "stage": "REFUNDED",
                    "facts": {
                        "transaction_id": case.transaction_id,
                        "amount": step.args.get("amount"),
                    },
                },
                purpose="Confirm the approved outcome",
            ),
            ProposedAction(tool="send_message", args={}, purpose="Notify the merchant"),
        ]
        goal_conditions.append(GoalCondition(kind="MESSAGE_SENT", params={}))

        plan = Plan(
            goal="The approved action is executed and independently verified.",
            steps=steps,
            goal_conditions=goal_conditions,
            primary_step=0,
            trigger="HUMAN_APPROVED",
        )
        ctx.plan = plan
        ctx.outputs = {}
        ctx.decisions = {0: decision}
        for index, extra in enumerate(steps[1:], start=1):
            spec = self.registry.get(extra.tool)
            if spec.side_effecting and spec.requires_policy:
                ctx.decisions[index] = await self.policy.evaluate(session, extra, policy_ctx)

        case.plan = plan.model_dump(mode="json")
        case.plan_cursor = 0
        return StepOutcome(CaseStatus.ACTING, "executing the human-approved action")

    async def _resume_after_rejection(
        self, session, case: Case, ctx: CaseRunContext
    ) -> StepOutcome:
        escalation = await self.escalation.latest_for_case(session, case.id)
        note = ""
        if escalation is not None and escalation.human_decision:
            note = escalation.human_decision.get("note") or ""

        await self._notify(
            session,
            case,
            ctx,
            stage="REJECTED",
            facts={"transaction_id": case.transaction_id, "note": note},
        )
        case.resolution = Resolution.HUMAN_REJECTED
        if self.workflows is not None:
            await self._start_memory_ingestion(session, case)
        return StepOutcome(CaseStatus.RESOLVED, "a specialist declined the request")

    # ------------------------------------------------------------------
    async def _notify(self, session, case: Case, ctx: CaseRunContext, *, stage: str, facts: dict):
        from ..services import ops_service

        tool_ctx = ToolContext(session=session, case=case, simulation=self.simulation, runtime=self)
        from .messaging import draft_for_stage

        body, claims, language = await draft_for_stage(tool_ctx, stage, facts)
        message = await ops_service.draft_message(
            session,
            case_id=case.id,
            content=body,
            meta={"stage": stage, "claims": claims, "language": language},
        )
        await record_event(
            session,
            case,
            EventType.MESSAGE_DRAFTED,
            message="Merchant reply drafted",
            result={"id": message.id},
        )
        await ops_service.send_message(session, message.id)
        await record_event(
            session,
            case,
            EventType.MESSAGE_SENT,
            message="Merchant notified",
            result={"id": message.id},
        )
        return message

    async def _policy_context(self, session, case: Case, ctx: CaseRunContext) -> PolicyContext:
        merchant = await ledger_service.get_merchant(session, case.merchant_id)
        txn = settlement = None
        disputes: list[Any] = []
        refunds: list[Any] = []
        if case.transaction_id:
            try:
                txn = await ledger_service.get_transaction(session, case.transaction_id)
                settlement = await ledger_service.get_settlement(session, case.transaction_id)
            except EnterpriseAPIError:
                pass
            disputes = await ledger_service.get_disputes(session, case.transaction_id)
            refunds = await refund_service.list_refunds_for_transaction(session, case.transaction_id)

        override = None
        if case.human_override:
            from ..schemas.agent import HumanOverride

            override = HumanOverride.model_validate(case.human_override)

        return PolicyContext(
            merchant=merchant,
            now=utcnow(),
            transaction=txn,
            settlement=settlement,
            disputes=disputes,
            diagnosis=ctx.diagnosis,
            existing_refunds=refunds,
            human_override=override,
        )

    # ------------------------------------------------------------------
    async def _start_scheduled_refund(self, session, case: Case) -> None:
        scheduled = await refund_service.find_scheduled_refund_for_case(session, case.id)
        if scheduled is None:
            return
        await self.workflows.start(
            session,
            "scheduled_refund",
            {"case_id": case.id, "refund_id": scheduled.id},
            case=case,
        )

    async def _start_memory_ingestion(self, session, case: Case) -> None:
        await self.workflows.start(
            session, "case_memory_ingestion", {"case_id": case.id}, case=case
        )

    async def _start_human_approval(self, session, case: Case, escalation) -> None:
        await self.workflows.start(
            session,
            "human_approval",
            {"case_id": case.id, "escalation_id": escalation.id},
            case=case,
        )


def _synthetic_action(case: Case):
    from ..database.models import Action

    return Action(
        id=f"ACT-VERIFY-{case.id}",
        case_id=case.id,
        action_type="verification",
        status="FAILED",
        attempt=1,
        step_index=case.plan_cursor,
    )


def _humanise(value: str) -> str:
    return value.replace("_", " ").lower()


def _eta_phrase(seconds: int) -> str:
    if seconds < 0:
        overdue = abs(seconds)
        hours, remainder = divmod(overdue, 3600)
        minutes = remainder // 60
        return f"overdue by {hours}h {minutes:02d}m" if hours else f"overdue by {minutes}m"
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"ETA {hours}h {minutes:02d}m" if hours else f"ETA {minutes}m"


def _unused(value: datetime) -> None:  # pragma: no cover
    return None
