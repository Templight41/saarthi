"""Merchant message drafting, with the claims guard.

The specification is explicit: never tell a merchant a refund has completed
unless backend verification confirms it. That rule cannot live in a prompt,
because a prompt is advisory. It lives here, where an unverifiable claim is
rejected and the message is redrafted from a template.

Each merchant is written to in their own language, and the language the draft
actually came out in is returned with it. That matters because the template
fallback below is English whatever the merchant speaks: recording the language
of the *message* rather than of the merchant is what keeps the spoken audio and
the text on screen the same words.
"""

from __future__ import annotations

import logging

from ..database.enums import RefundStatus, SettlementStatus
from ..llm.mock import MockProvider
from ..schemas.agent import MessageDraft
from ..services import ledger_service, refund_service
from ..tools.registry import ToolContext
from ..voice import languages

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
- Never name an internal process, state or failure mode. "Identification
  failed" means nothing to a merchant.
- Never describe the merchant's account as held, suspended, under verification
  or restricted unless the facts say so. Inventing a reassuring-sounding
  explanation for a gap is the worst thing you can do here.
"""

# Claims that require independent backend confirmation before they may be made.
VERIFIABLE_CLAIMS = {"REFUND_COMPLETED", "SETTLEMENT_COMPLETED"}

# The template fallback in llm/mock.py is written in English only.
TEMPLATE_LANGUAGE = "en-IN"



async def draft_for_stage(
    ctx: ToolContext, stage: str, facts: dict
) -> tuple[str, list[str], str]:
    """Returns the body, the claims it is allowed to make, and its language."""
    runtime = ctx.runtime
    provider = getattr(runtime, "llm", None) or _FALLBACK
    language = await _merchant_language(ctx)
    # The template engine writes English whoever it is standing in for, so a
    # simulated provider means an English body no matter what the merchant
    # speaks. Recording the merchant's language here would make the audio and
    # the text disagree.
    if getattr(provider, "simulated", False):
        language = TEMPLATE_LANGUAGE

    context = {"stage": stage, "facts": facts, "language": language}
    try:
        draft = await provider.complete_json(
            system=_system_prompt(language),
            user=_user_prompt(stage, facts, language),
            schema=MessageDraft,
            context=context,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Message drafting failed (%s); using template", exc)
        draft = await _FALLBACK.complete_json(
            system=SYSTEM_PROMPT, user="", schema=MessageDraft, context=context
        )
        language = TEMPLATE_LANGUAGE

    allowed, rejected = await _verify_claims(ctx, draft.claims, facts)
    if rejected:
        logger.warning("Rejected unverified claims %s; redrafting from template", rejected)
        safe = await _FALLBACK.complete_json(
            system=SYSTEM_PROMPT,
            user="",
            schema=MessageDraft,
            context={"stage": _downgrade_stage(stage), "facts": facts},
        )
        return safe.body, safe.claims, TEMPLATE_LANGUAGE
    return draft.body, allowed, language


async def _merchant_language(ctx: ToolContext) -> str:
    """This case's language if it has one, else the merchant's default.

    A merchant served in Hindi can still raise one case in Tamil, so the case
    wins where it says anything.
    """
    chosen = getattr(ctx.case, "language", None)
    if chosen:
        return languages.normalise(chosen, default=TEMPLATE_LANGUAGE)
    try:
        merchant = await ledger_service.get_merchant(ctx.session, ctx.case.merchant_id)
    except Exception:  # noqa: BLE001
        return TEMPLATE_LANGUAGE
    return languages.normalise(merchant.language, default=TEMPLATE_LANGUAGE)


def _system_prompt(language: str) -> str:
    if language == TEMPLATE_LANGUAGE:
        return SYSTEM_PROMPT
    name = languages.name_of(language)
    return (
        f"{SYSTEM_PROMPT}- Write the message in {name}. Keep ₹ amounts and "
        "transaction ids exactly as given, in Latin script.\n"
    )


def _downgrade_stage(stage: str) -> str:
    """If a completion claim could not be verified, fall back to an interim tone."""
    return "INTERIM" if stage in {"REFUNDED", "SETTLED"} else stage


def _user_prompt(stage: str, facts: dict, language: str) -> str:
    if stage == "FOLLOW_UP":
        return (
            "The merchant asked a follow-up question while their case was still open. "
            f"Answer it directly from these facts and nothing else: {facts}\n"
            f"Write it in {languages.name_of(language)}.\n"
            "Answer the question asked. Do not promise a time the facts do not give, "
            "and do not say anything has completed unless the facts say so.\n"
            "Return JSON matching the schema, listing in `claims` only what the facts support."
        )
    return (
        f"Write the merchant message for stage {stage}.\n"
        f"Only these facts are true: {facts}\n"
        f"Write it in {languages.name_of(language)}.\n"
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
    """Look the refund up independently. The planner cannot know its id in
    advance, so fall back to the transaction's refunds rather than rejecting a
    claim that is in fact true."""
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

    if refund is None:
        txn_id = facts.get("transaction_id") or ctx.case.transaction_id
        if not txn_id:
            return False
        refunds = await refund_service.list_refunds_for_transaction(ctx.session, txn_id)
        completed = [r for r in refunds if r.status == RefundStatus.COMPLETED]
        if not completed:
            return False
        # If the message names an amount, that exact refund must have completed.
        amount = facts.get("amount")
        if amount is not None:
            from decimal import Decimal

            wanted = Decimal(str(amount))
            return any(Decimal(r.amount) == wanted for r in completed)
        return True

    return refund.status == RefundStatus.COMPLETED


async def _settlement_is_complete(ctx: ToolContext, facts: dict) -> bool:
    txn_id = facts.get("transaction_id") or ctx.case.transaction_id
    if not txn_id:
        return False
    try:
        settlement = await ledger_service.get_settlement(ctx.session, txn_id)
    except Exception:  # noqa: BLE001
        return False
    return settlement.status == SettlementStatus.COMPLETED
