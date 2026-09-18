"""The authorization boundary.

The model proposes; this decides. Combination order matters:

  1. A DENY from a non-overridable rule wins outright, even with human approval.
  2. A human override downgrades overridable REQUIRES_APPROVAL/DENY to ALLOW,
     recording APPROVED_BY_HUMAN first in the policy list so the audit trail
     shows exactly what was overridden and by whom.
  3. Otherwise any DENY wins, then any REQUIRES_APPROVAL, else ALLOW.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import Policy
from ..schemas.agent import PolicyDecision, PolicyDecisionType, PolicyRuleResult, ProposedAction
from .rules import RULE_CLASSES, PolicyContext, PolicyRule

HUMAN_OVERRIDE_ID = "APPROVED_BY_HUMAN"


class PolicyEngine:
    def __init__(self, rule_classes: dict[str, type[PolicyRule]] | None = None) -> None:
        self._rule_classes = rule_classes or RULE_CLASSES

    async def load_rules(self, session: AsyncSession) -> list[PolicyRule]:
        rows = await session.scalars(
            select(Policy).where(Policy.active.is_(True)).order_by(Policy.priority)
        )
        rules: list[PolicyRule] = []
        for row in rows:
            cls = self._rule_classes.get(row.policy_type)
            if cls is None:
                continue
            rules.append(cls(row.id, row.configuration or {}))
        return rules

    async def evaluate(
        self, session: AsyncSession, action: ProposedAction, ctx: PolicyContext
    ) -> PolicyDecision:
        rules = await self.load_rules(session)
        results: list[PolicyRuleResult] = []
        for rule in rules:
            outcome = rule.evaluate(action, ctx)
            if outcome is not None:
                results.append(outcome)

        overridable = {r.policy_type: r for r in rules}

        # 1. Non-overridable denials are absolute.
        hard_denials = [
            r
            for r in results
            if r.decision == PolicyDecisionType.DENY
            and not overridable[r.policy_type].overridable_by_human
        ]
        if hard_denials:
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reasons=[r.reason for r in hard_denials],
                policy_ids=[r.policy_id for r in hard_denials],
                evaluated=results,
                human_override=ctx.human_override,
            )

        # 2. Human override downgrades the rest.
        if ctx.human_override is not None:
            overridden = [
                r.policy_id
                for r in results
                if r.decision in {PolicyDecisionType.REQUIRES_APPROVAL, PolicyDecisionType.DENY}
            ]
            reasons = [
                f"Human approval {ctx.human_override.escalation_id} by "
                f"{ctx.human_override.decided_by} overrides "
                f"{', '.join(overridden) if overridden else 'no blocking policy'}"
            ]
            reasons += [r.reason for r in results if r.decision == PolicyDecisionType.ALLOW]
            return PolicyDecision(
                decision=PolicyDecisionType.ALLOW,
                reasons=reasons,
                policy_ids=[HUMAN_OVERRIDE_ID, *[r.policy_id for r in results]],
                evaluated=results,
                human_override=ctx.human_override,
            )

        # 3. Normal combination.
        denials = [r for r in results if r.decision == PolicyDecisionType.DENY]
        if denials:
            return PolicyDecision(
                decision=PolicyDecisionType.DENY,
                reasons=[r.reason for r in denials],
                policy_ids=[r.policy_id for r in denials],
                evaluated=results,
            )

        approvals = [r for r in results if r.decision == PolicyDecisionType.REQUIRES_APPROVAL]
        if approvals:
            return PolicyDecision(
                decision=PolicyDecisionType.REQUIRES_APPROVAL,
                reasons=[r.reason for r in approvals],
                policy_ids=[r.policy_id for r in approvals],
                evaluated=results,
            )

        allows = [r for r in results if r.decision == PolicyDecisionType.ALLOW]
        return PolicyDecision(
            decision=PolicyDecisionType.ALLOW,
            reasons=[r.reason for r in allows] or ["No policy restricts this action"],
            policy_ids=[r.policy_id for r in allows],
            evaluated=results,
        )
