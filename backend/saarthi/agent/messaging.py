"""Merchant message drafting, with the claims guard.

The specification is explicit: never tell a merchant a refund has completed
unless backend verification confirms it. That rule cannot live in a prompt,
because a prompt is advisory. It lives here, where an unverifiable claim is
rejected and the message is redrafted from a template.
"""

from __future__ import annotations

import logging

from ..database.enums import RefundStatus, SettlementStatus
from ..llm.mock import MockProvider
from ..schemas.agent import MessageDraft
from ..services import ledger_service, refund_service
from ..tools.registry import ToolContext

logger = logging.getLogger(__name__)

_FALLBACK = MockProvider()

SYSTEM_PROMPT = """You are Saarthi, an autonomous merchant operations teammate for an Indian
payments platform. You write short, factual messages to merchants.

Rules:
- Never claim a refund, settlement or payment has completed unless the facts you
  are given say so explicitly.
- Use rupee amounts with the ₹ symbol.
- Two to four sentences. No greetings, no sign-off, no emoji.
- Say what you did, what is true now, and what happens next.
"""

# Claims that require independent backend confirmation before they may be made.
VERIFIABLE_CLAIMS = {"REFUND_COMPLETED", "SETTLEMENT_COMPLETED"}


async def draft_for_stage(ctx: ToolContext, stage: str, facts: dict) -> tuple[str, list[str]]:
    runtime = ctx.runtime
    provider = getattr(runtime, "llm", None) or _FALLBACK

    context = {"stage": stage, "facts": facts}
    try:
        draft = await provider.complete_json(
            system=SYSTEM_PROMPT,
            user=_user_prompt(stage, facts),
            schema=MessageDraft,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Message drafting failed (%s); using template", exc)
        draft = await _FALLBACK.complete_json(
            system=SYSTEM_PROMPT, user="", schema=MessageDraft, context=context
        )

    allowed, rejected = await _verify_claims(ctx, draft.claims, facts)
    if rejected:
        logger.warning("Rejected unverified claims %s; redrafting from template", rejected)
        safe = await _FALLBACK.complete_json(
            system=SYSTEM_PROMPT,
            user="",
            schema=MessageDraft,
            context={"stage": _downgrade_stage(stage), "facts": facts},
        )
        return safe.body, safe.claims
    return draft.body, allowed


def _downgrade_stage(stage: str) -> str:
    """If a completion claim could not be verified, fall back to an interim tone."""
    return "INTERIM" if stage in {"REFUNDED", "SETTLED"} else stage


def _user_prompt(stage: str, facts: dict) -> str:
    return (
        f"Write the merchant message for stage {stage}.\n"
        f"Only these facts are true: {facts}\n"
        "Return JSON matching the schema, listing in `claims` only what the facts support."
    )


async def _verify_claims(
    ctx: ToolContext, claims: list[str], facts: dict
) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    rejected: list[str] = []

    for claim in claims:
        if claim not in VERIFIABLE_CLAIMS:
            allowed.append(claim)
            continue
        if claim == "REFUND_COMPLETED":
            ok = await _refund_is_complete(ctx, facts)
        else:
            ok = await _settlement_is_complete(ctx, facts)
        (allowed if ok else rejected).append(claim)
    return allowed, rejected


async def _refund_is_complete(ctx: ToolContext, facts: dict) -> bool:
    refund_id = facts.get("refund_id")
    key = facts.get("idempotency_key")
    refund = None
    if refund_id:
        try:
            refund = await refund_service.get_refund(ctx.session, refund_id)
        except Exception:  # noqa: BLE001
            refund = None
    if refund is None and key:
        refund = await refund_service.find_refund_by_idempotency_key(ctx.session, key)
    return refund is not None and refund.status == RefundStatus.COMPLETED


async def _settlement_is_complete(ctx: ToolContext, facts: dict) -> bool:
    txn_id = facts.get("transaction_id") or ctx.case.transaction_id
    if not txn_id:
        return False
    try:
        settlement = await ledger_service.get_settlement(ctx.session, txn_id)
    except Exception:  # noqa: BLE001
        return False
    return settlement.status == SettlementStatus.COMPLETED
