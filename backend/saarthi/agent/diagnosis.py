"""Diagnosis, then fact clamping.

The model classifies the merchant's problem. Deterministic rules then override
it wherever the database disagrees, because the database is authoritative and
a confident wrong diagnosis is worse than a cautious one. Every clamp is
recorded so the correction is visible rather than silent.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ..database.enums import DisputeStatus, RiskLevel
from ..schemas.agent import Diagnosis, Intent, RootCause
from .context import CaseContext

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Saarthi, an autonomous merchant operations teammate for an Indian
payments platform. Classify the merchant's problem from their message and the
current business state.

Critical rules:
- The CONTEXT block is authoritative current state. Historical memory is advisory
  only and must never override it.
- `merchant_patterns_advisory_only` counts what has happened to this merchant
  before. It is real, but it is history: it may explain a problem and it may
  raise its priority, and it never establishes what is true of this payment. A
  merchant with three past settlement delays can still have a successful one.
- A pending payment where the customer was debited is NOT a confirmed failure.
  Check the settlement state before concluding a payment failed.
- If you cannot classify the request confidently, return UNKNOWN with a low
  confidence rather than inventing certainty.
- `requested_amount` must come from the merchant's message or an existing
  dispute, never invented.
- Cite the specific facts you relied on in `evidence`.
"""


class Diagnoser:
    def __init__(self, llm, registry=None) -> None:
        self._llm = llm
        self._registry = registry

    async def diagnose(self, message: str, ctx: CaseContext) -> Diagnosis:
        diagnosis = await self._llm.complete_json(
            system=SYSTEM_PROMPT,
            user=f"Merchant message:\n{message}",
            schema=Diagnosis,
            context=ctx.for_prompt(),
        )
        return clamp_to_facts(diagnosis, ctx, registry=self._registry)


def clamp_to_facts(diagnosis: Diagnosis, ctx: CaseContext, *, registry=None) -> Diagnosis:
    """Override the model wherever the database contradicts it."""
    clamped: list[str] = []
    txn = ctx.transaction or {}
    settlement = ctx.settlement or {}

    # The identified transaction is a fact, not a model opinion.
    if txn.get("id") and diagnosis.transaction_id != txn["id"]:
        diagnosis.transaction_id = txn["id"]
        clamped.append("transaction_id")

    payment_status = txn.get("payment_status")
    debited = bool(txn.get("customer_debited"))
    settlement_status = settlement.get("status")

    # An announcement the ledger cannot confirm is never a payment.
    #
    # Deliberately *not* conditional on what the model called this case. Live
    # models classify the same Soundbox complaint as a settlement delay about
    # whichever real pending transaction happens to be nearby, which silently
    # drops the announcement that has no payment behind it at all. A phantom
    # payment is a fact about the ledger, so the ledger decides, and the model
    # is told afterwards.
    phantom_announcements = [
        n
        for n in ctx.notifications
        if n.get("outcome") == "NO_AUTHORITATIVE_RECORD"
        # One the identified transaction accounts for is not a phantom.
        and n.get("reference") != txn.get("id")
    ]
    if phantom_announcements:
        if diagnosis.root_cause != RootCause.ANNOUNCEMENT_WITHOUT_PAYMENT:
            diagnosis.root_cause = RootCause.ANNOUNCEMENT_WITHOUT_PAYMENT
            clamped.append("root_cause")
        if diagnosis.intent != Intent.NOTIFICATION_MISMATCH:
            diagnosis.intent = Intent.NOTIFICATION_MISMATCH
            clamped.append("intent")
        # Nothing autonomous is safe here: every option begins by assuming the
        # payment exists, which is precisely what is in doubt.
        if not diagnosis.requires_human:
            diagnosis.requires_human = True
            clamped.append("requires_human")
    elif (
        diagnosis.intent == Intent.NOTIFICATION_MISMATCH
        and ctx.notifications
        and all(n.get("confirmed_by_ledger") for n in ctx.notifications)
    ):
        # Every announcement checks out. The discrepancy was the dashboard,
        # not the money, and there is nothing to do about the money.
        if diagnosis.root_cause != RootCause.NO_ISSUE_FOUND:
            diagnosis.root_cause = RootCause.NO_ISSUE_FOUND
            clamped.append("root_cause")

    # A debited-but-pending payment is a settlement delay, never a confirmed
    # failure — unless a phantom announcement means that is not the question.
    if (
        payment_status == "PAYMENT_PENDING"
        and debited
        and settlement_status in {"PENDING", "OVERDUE"}
        and not phantom_announcements
    ):
        if diagnosis.root_cause != RootCause.SETTLEMENT_DELAY:
            diagnosis.root_cause = RootCause.SETTLEMENT_DELAY
            clamped.append("root_cause")
        if diagnosis.intent not in {
            Intent.PAYMENT_DEBITED_BUT_NOT_CONFIRMED,
            Intent.SETTLEMENT_DELAY,
        }:
            diagnosis.intent = Intent.PAYMENT_DEBITED_BUT_NOT_CONFIRMED
            clamped.append("intent")

    # An open subjective dispute forces that intent and a human.
    open_quality = [
        d
        for d in ctx.disputes
        if d.get("type") == "PRODUCT_QUALITY"
        and d.get("status") in {DisputeStatus.OPEN.value, DisputeStatus.UNDER_REVIEW.value}
    ]
    if open_quality:
        if diagnosis.intent != Intent.PRODUCT_QUALITY_DISPUTE:
            diagnosis.intent = Intent.PRODUCT_QUALITY_DISPUTE
            clamped.append("intent")
        if diagnosis.root_cause != RootCause.PRODUCT_QUALITY_DISPUTE:
            diagnosis.root_cause = RootCause.PRODUCT_QUALITY_DISPUTE
            clamped.append("root_cause")
        if not diagnosis.requires_human:
            diagnosis.requires_human = True
            clamped.append("requires_human")
        if diagnosis.requested_amount is None and open_quality[0].get("requested_amount"):
            diagnosis.requested_amount = Decimal(str(open_quality[0]["requested_amount"]))
            clamped.append("requested_amount")

    # A requested amount over the merchant's authority is at least medium risk.
    limit = Decimal(str(ctx.merchant.get("autonomous_refund_limit", "0")))
    if diagnosis.requested_amount is not None and diagnosis.requested_amount > limit:
        if diagnosis.risk == RiskLevel.LOW:
            diagnosis.risk = RiskLevel.MEDIUM
            clamped.append("risk")

    # A refund cannot exceed what is left on the transaction.
    if diagnosis.requested_amount is not None and txn.get("amount"):
        remaining = Decimal(str(txn["amount"])) - Decimal(str(txn.get("refunded_amount", "0")))
        if diagnosis.requested_amount > remaining:
            diagnosis.requested_amount = remaining
            clamped.append("requested_amount")

    # Suggested actions are advisory: keep only real tool names, never execute.
    if registry is not None and diagnosis.suggested_actions:
        known = registry.names()
        filtered = [a for a in diagnosis.suggested_actions if a in known]
        if filtered != diagnosis.suggested_actions:
            diagnosis.suggested_actions = filtered
            clamped.append("suggested_actions")

    diagnosis.clamped_fields = clamped
    if clamped:
        logger.info("Diagnosis clamped to facts: %s", clamped)
    return diagnosis
