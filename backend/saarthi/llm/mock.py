"""Deterministic provider.

This is not a stub. It is an ordered rule table over the merchant's words and
the current transaction facts, and it is what lets the entire demo run with no
API key. It is also the fallback for every real provider.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel

from ..database.enums import RiskLevel
from ..schemas.agent import Diagnosis, Intent, MessageDraft, RootCause
from .base import LLMError, LLMProvider

T = TypeVar("T", bound=BaseModel)

_AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_BARE_AMOUNT = re.compile(r"\b(\d{3,6}(?:,\d{3})*(?:\.\d{1,2})?)\b")

_HUMAN = re.compile(r"\b(human|real person|agent|manager|someone|supervisor)\b", re.IGNORECASE)
_HUMAN_VERB = re.compile(r"\b(speak|talk|connect|escalate|transfer)\b", re.IGNORECASE)
_QUALITY = re.compile(r"\b(quality|defect|damaged|poor|faulty|broken|not as described)\b", re.IGNORECASE)
_DEBITED = re.compile(r"\b(deducted|debited|charged|money (?:was|got) taken)\b", re.IGNORECASE)
_FAILED = re.compile(r"\b(failed|failure|pending|not confirmed|stuck|didn'?t go through)\b", re.IGNORECASE)
_REFUND = re.compile(r"\brefund\b", re.IGNORECASE)
_STATUS_Q = re.compile(r"\b(status|where|when|how long|update)\b", re.IGNORECASE)
_CANCEL = re.compile(r"\b(cancel|cancelled|returned|return)\b", re.IGNORECASE)
_SETTLEMENT = re.compile(r"\b(settle|settlement|payout|not received)\b", re.IGNORECASE)


def _extract_amount(text: str) -> Decimal | None:
    match = _AMOUNT.search(text)
    if match:
        return Decimal(match.group(1).replace(",", ""))
    match = _BARE_AMOUNT.search(text)
    if match:
        return Decimal(match.group(1).replace(",", ""))
    return None


class MockProvider(LLMProvider):
    name = "mock"
    simulated = True

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        context: dict | None = None,
        temperature: float = 0.0,
    ) -> T:
        context = context or {}
        if schema is Diagnosis:
            return self._diagnose(user, context)  # type: ignore[return-value]
        if schema is MessageDraft:
            return self._draft(context)  # type: ignore[return-value]
        raise LLMError(f"MockProvider has no rule for schema {schema.__name__}")

    async def complete_text(self, *, system: str, user: str) -> str:
        return user

    # ------------------------------------------------------------------
    def _diagnose(self, message: str, ctx: dict) -> Diagnosis:
        txn = ctx.get("transaction") or {}
        settlement = ctx.get("settlement") or {}
        disputes = ctx.get("disputes") or []
        txn_id = txn.get("id")
        amount = _extract_amount(message)

        payment_status = txn.get("payment_status")
        debited = bool(txn.get("customer_debited"))
        settlement_status = settlement.get("status")

        open_quality_dispute = any(
            d.get("type") == "PRODUCT_QUALITY" and d.get("status") in {"OPEN", "UNDER_REVIEW"}
            for d in disputes
        )

        # 1. Explicit request for a human always wins.
        if _HUMAN.search(message) and _HUMAN_VERB.search(message):
            return Diagnosis(
                intent=Intent.GENERAL_TRANSACTION_QUERY,
                transaction_id=txn_id,
                root_cause=RootCause.UNKNOWN,
                confidence=0.90,
                risk=RiskLevel.MEDIUM,
                requires_human=True,
                merchant_requests_human=True,
                summary="Merchant explicitly asked to speak to a person.",
                evidence=["merchant requested a human"],
            )

        # 2. Subjective product-quality dispute.
        if open_quality_dispute or _QUALITY.search(message):
            requested = amount
            if requested is None and disputes:
                raw = disputes[0].get("requested_amount")
                requested = Decimal(str(raw)) if raw is not None else None
            return Diagnosis(
                intent=Intent.PRODUCT_QUALITY_DISPUTE,
                transaction_id=txn_id,
                root_cause=RootCause.PRODUCT_QUALITY_DISPUTE,
                confidence=0.90,
                risk=RiskLevel.HIGH,
                requires_human=True,
                requested_amount=requested,
                summary="Customer disputes product quality and wants money back. Subjective judgement.",
                evidence=["product-quality dispute", f"requested amount {requested}"],
                suggested_actions=["issue_refund"],
            )

        # 3. Debited but not confirmed / settlement still pending.
        pending_and_debited = payment_status == "PAYMENT_PENDING" and debited
        if pending_and_debited or (_DEBITED.search(message) and _FAILED.search(message)):
            return Diagnosis(
                intent=Intent.PAYMENT_DEBITED_BUT_NOT_CONFIRMED,
                transaction_id=txn_id,
                root_cause=RootCause.SETTLEMENT_DELAY,
                confidence=0.94,
                risk=RiskLevel.LOW,
                requires_human=False,
                summary=(
                    "Customer debit is confirmed but the payment has not been confirmed as failed. "
                    "Settlement is still pending, so this is most likely a settlement delay."
                ),
                evidence=[
                    f"customer_debited={debited}",
                    f"payment_status={payment_status}",
                    f"settlement={settlement_status}",
                ],
                suggested_actions=["schedule_refund", "send_message"],
            )

        # 4. Refund status query.
        if _REFUND.search(message) and _STATUS_Q.search(message):
            return Diagnosis(
                intent=Intent.REFUND_STATUS,
                transaction_id=txn_id,
                root_cause=RootCause.REFUND_IN_PROGRESS,
                confidence=0.88,
                risk=RiskLevel.LOW,
                summary="Merchant is asking about the status of a refund.",
                evidence=["refund status query"],
            )

        # 5. Refund request.
        if _REFUND.search(message) or _CANCEL.search(message):
            requested = amount or (Decimal(str(txn["amount"])) if txn.get("amount") else None)
            return Diagnosis(
                intent=Intent.REFUND_REQUEST,
                transaction_id=txn_id,
                root_cause=RootCause.CUSTOMER_REFUND_REQUEST,
                confidence=0.91,
                risk=RiskLevel.LOW,
                requested_amount=requested,
                summary="Customer cancelled or returned the order and the merchant wants a refund issued.",
                evidence=[f"requested amount {requested}", f"payment_status={payment_status}"],
                suggested_actions=["issue_refund"],
            )

        # 6. Settlement chase with no debit language.
        if _SETTLEMENT.search(message) and settlement_status == "PENDING":
            return Diagnosis(
                intent=Intent.SETTLEMENT_DELAY,
                transaction_id=txn_id,
                root_cause=RootCause.SETTLEMENT_DELAY,
                confidence=0.89,
                risk=RiskLevel.LOW,
                summary="Settlement for this transaction has not arrived yet.",
                evidence=[f"settlement={settlement_status}"],
            )

        # 7. Everything healthy: no action needed.
        if payment_status == "SUCCESS" and settlement_status == "COMPLETED":
            return Diagnosis(
                intent=Intent.GENERAL_TRANSACTION_QUERY,
                transaction_id=txn_id,
                root_cause=RootCause.NO_ISSUE_FOUND,
                confidence=0.96,
                risk=RiskLevel.LOW,
                summary="This transaction was paid successfully and has already settled. Nothing is wrong.",
                evidence=["payment_status=SUCCESS", "settlement=COMPLETED"],
            )

        # 8. Genuinely unclear. Low confidence deliberately triggers escalation
        #    rather than fabricating certainty.
        return Diagnosis(
            intent=Intent.UNKNOWN,
            transaction_id=txn_id,
            root_cause=RootCause.UNKNOWN,
            confidence=0.35,
            risk=RiskLevel.MEDIUM,
            summary="The request could not be classified with enough confidence.",
            evidence=["no matching pattern"],
        )

    # ------------------------------------------------------------------
    def _draft(self, ctx: dict) -> MessageDraft:
        stage = ctx.get("stage", "OUTCOME")
        facts = ctx.get("facts", {})
        txn_id = facts.get("transaction_id", "the transaction")
        amount = facts.get("amount")
        amount_str = f"₹{amount}" if amount else "the amount"

        if stage == "INTERIM":
            eta = facts.get("eta_text", "shortly")
            return MessageDraft(
                body=(
                    f"I've checked {txn_id}. Your customer's debit of {amount_str} is confirmed, but the "
                    f"payment has not been confirmed as failed. It is still pending settlement with the "
                    f"bank, expected {eta}. I'm monitoring it and will refund automatically if it does not "
                    f"settle in time. I'll update you either way."
                ),
                tone="reassuring",
                claims=["SETTLEMENT_PENDING"],
            )
        if stage == "SETTLED":
            return MessageDraft(
                body=(
                    f"Good news: settlement for {txn_id} has now completed and {amount_str} is settled. "
                    f"No refund was needed, so I've cancelled the standby refund. Nothing further is "
                    f"required from you."
                ),
                tone="informational",
                claims=["SETTLEMENT_COMPLETED"],
            )
        if stage == "REFUNDED":
            ref = facts.get("refund_id", "")
            return MessageDraft(
                body=(
                    f"The {amount_str} refund for {txn_id} has completed and I've verified it against the "
                    f"refund ledger{f' (reference {ref})' if ref else ''}. Your customer should see it "
                    f"back on their original payment method shortly."
                ),
                tone="informational",
                claims=["REFUND_COMPLETED"],
            )
        if stage == "ESCALATED":
            reason = facts.get("reason_text", "it needs a specialist's judgement")
            return MessageDraft(
                body=(
                    f"I've gathered everything on {txn_id}, but I'm not authorised to complete this one on "
                    f"my own because {reason}. I've passed it to a specialist with the full transaction "
                    f"history, dispute record and policy position, so they can decide quickly."
                ),
                tone="apologetic",
                claims=["ESCALATED"],
            )
        if stage == "REJECTED":
            note = facts.get("note", "")
            return MessageDraft(
                body=(
                    f"A specialist has reviewed the request on {txn_id} and decided not to approve it. "
                    f"{note} If you'd like to contest that, reply here and I'll reopen the case."
                ).strip(),
                tone="apologetic",
                claims=["ESCALATED"],
            )
        if stage == "NO_ISSUE":
            return MessageDraft(
                body=(
                    f"I checked {txn_id}: the payment of {amount_str} succeeded and the settlement has "
                    f"already completed. There's nothing wrong with this transaction and no action is "
                    f"needed."
                ),
                tone="informational",
                claims=["NO_ISSUE"],
            )
        return MessageDraft(
            body=f"I've updated the case for {txn_id}.",
            tone="informational",
            claims=[],
        )
