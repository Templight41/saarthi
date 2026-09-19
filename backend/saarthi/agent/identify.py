"""Entity resolution.

Cheap deterministic methods first: an explicit hint, then an ID in the text,
then explicit rules over the merchant's transactions. No model, because
getting this wrong sends the whole case off course — and the failure is silent,
which is worse. A case that investigates the wrong transaction still produces a
confident diagnosis, a policy decision and a merchant message, all about the
wrong money.

These rules replace an earlier cascade of "if exactly one transaction is
pending, take it; otherwise if exactly one is disputed, take that". That had
two faults. It ignored the amount the merchant had just told us, which is the
single strongest signal available. And ambiguity in one category promoted an
unrelated one: with two pending payments and one open dispute, "the customer
paid ₹2,500 but it is still pending" resolved to the ₹15,000 quality dispute.

Ambiguity must narrow confidence, never widen the search. When nothing wins
clearly this returns AMBIGUOUS, and the supervisor escalates rather than
picking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import UNSETTLED_SETTLEMENT, PaymentStatus
from ..services import ledger_service

TXN_PATTERN = re.compile(r"\b(TXN[A-Z0-9_]+)\b", re.IGNORECASE)

# "₹2,500", "Rs. 2500", "INR 2500", or a bare 3-6 digit number.
_AMOUNT = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]+(?:\.\d{1,2})?)", re.IGNORECASE)
_BARE_AMOUNT = re.compile(r"\b(\d{3,7}(?:,\d{3})*(?:\.\d{1,2})?)\b")

_DISPUTE_WORDS = re.compile(
    r"\b(dispute|quality|defect|damaged|faulty|broken|not as described|complain)\b",
    re.IGNORECASE,
)
_REFUND_WORDS = re.compile(r"\b(refund|return|cancel|cancelled|money back)\b", re.IGNORECASE)
_PENDING_WORDS = re.compile(
    r"\b(pending|failed|stuck|not confirmed|deducted|debited|didn'?t go through|"
    r"settle|settlement|payout|not received|not credited)\b",
    re.IGNORECASE,
)


@dataclass
class _Fact:
    txn: object
    unsettled: bool
    has_open_dispute: bool


def amount_in(message: str) -> Decimal | None:
    """The amount the merchant named, if they named one."""
    for pattern in (_AMOUNT, _BARE_AMOUNT):
        match = pattern.search(message)
        if match:
            try:
                return Decimal(match.group(1).replace(",", ""))
            except InvalidOperation:
                return None
    return None


def topics_in(message: str) -> set[str]:
    """What kind of problem the merchant is describing."""
    topics = set()
    if _DISPUTE_WORDS.search(message):
        topics.add("DISPUTE")
    if _REFUND_WORDS.search(message):
        topics.add("REFUND")
    if _PENDING_WORDS.search(message):
        topics.add("PENDING")
    return topics


async def resolve_transaction(
    session: AsyncSession,
    merchant_id: str,
    message: str,
    *,
    hint: str | None = None,
) -> tuple[str | None, str, list[str]]:
    """Return (transaction_id, how, candidates)."""
    if hint:
        try:
            await ledger_service.get_transaction(session, hint)
            return hint, "EXPLICIT", [hint]
        except Exception:  # noqa: BLE001
            pass

    match = TXN_PATTERN.search(message)
    if match:
        candidate = match.group(1).upper()
        try:
            txn = await ledger_service.get_transaction(session, candidate)
            if txn.merchant_id == merchant_id:
                return txn.id, "REGEX", [txn.id]
        except Exception:  # noqa: BLE001
            pass

    transactions = await ledger_service.find_transactions_for_merchant(session, merchant_id)
    if not transactions:
        return None, "NONE", []
    if len(transactions) == 1:
        return transactions[0].id, "ONLY_ONE", [transactions[0].id]

    facts = await _describe(session, transactions)
    wanted = amount_in(message)
    topics = topics_in(message)

    by_amount = [f for f in facts if wanted is not None and Decimal(f.txn.amount) == wanted]
    on_topic = [f for f in facts if _matches_topic(f, topics)]

    # 1. The merchant named an amount and exactly one transaction has it. That
    #    is the strongest signal there is — unless the state contradicts what
    #    they described, in which case two different transactions each fit half
    #    of what was said and guessing between them is not ours to do.
    if len(by_amount) == 1:
        chosen = by_amount[0]
        contradicted = topics and not _matches_topic(chosen, topics)
        rival = [f for f in on_topic if f.txn.id != chosen.txn.id]
        if not (contradicted and rival):
            return chosen.txn.id, "AMOUNT", [chosen.txn.id]
        return (
            None,
            "AMBIGUOUS",
            [chosen.txn.id, *[f.txn.id for f in rival]][:5],
        )

    # 2. They described a kind of problem, and exactly one transaction is in
    #    that state.
    if topics and len(on_topic) == 1:
        return on_topic[0].txn.id, "TOPIC", [on_topic[0].txn.id]

    # 3. Nothing to go on but the ledger: if exactly one transaction is
    #    unresolved, that is almost certainly the one being asked about.
    #    Deliberately no equivalent fallback for disputes — falling through to
    #    "the only disputed one" is what used to answer a question about a
    #    pending ₹2,500 payment with a ₹15,000 quality dispute.
    unresolved = [f for f in facts if f.unsettled]
    if wanted is None and len(unresolved) == 1:
        return unresolved[0].txn.id, "SOLE_UNRESOLVED", [unresolved[0].txn.id]

    # Nothing won. Hand back the shortlist so the escalation can show a person
    # what was actually in contention.
    shortlist = [f.txn.id for f in (on_topic or by_amount or unresolved or facts)]
    return None, "AMBIGUOUS", shortlist[:5]


def _matches_topic(fact: _Fact, topics: set[str]) -> bool:
    if "PENDING" in topics and fact.unsettled:
        return True
    if "DISPUTE" in topics and fact.has_open_dispute:
        return True
    if "REFUND" in topics and fact.txn.payment_status == PaymentStatus.SUCCESS:
        return True
    return False


async def _describe(session: AsyncSession, transactions: list) -> list[_Fact]:
    """The two things about a transaction that a merchant's words can point at."""
    facts: list[_Fact] = []
    for txn in transactions:
        unsettled = txn.payment_status == PaymentStatus.PAYMENT_PENDING
        if not unsettled:
            try:
                settlement = await ledger_service.get_settlement(session, txn.id)
                unsettled = settlement.status in UNSETTLED_SETTLEMENT
            except Exception:  # noqa: BLE001
                unsettled = False

        disputes = await ledger_service.get_disputes(session, txn.id)
        facts.append(
            _Fact(
                txn=txn,
                unsettled=unsettled,
                has_open_dispute=any(
                    d.status.value in {"OPEN", "UNDER_REVIEW"} for d in disputes
                ),
            )
        )
    # Newest first, so any remaining tie resolves the same way every time.
    facts.sort(key=lambda f: f.txn.created_at, reverse=True)
    return facts
