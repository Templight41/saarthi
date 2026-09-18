"""Entity resolution.

Cheap deterministic methods first: an explicit hint, then an ID in the text,
then a heuristic over the merchant's open transactions. The model is only a
last resort, because getting this wrong sends the whole case off course.
"""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import PaymentStatus, SettlementStatus
from ..services import ledger_service

TXN_PATTERN = re.compile(r"\b(TXN[A-Z0-9_]+)\b", re.IGNORECASE)


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

    # Heuristic: the merchant is almost always talking about something unresolved.
    problematic = []
    for txn in transactions:
        if txn.payment_status == PaymentStatus.PAYMENT_PENDING:
            problematic.append(txn)
            continue
        try:
            settlement = await ledger_service.get_settlement(session, txn.id)
        except Exception:  # noqa: BLE001
            continue
        if settlement.status == SettlementStatus.PENDING:
            problematic.append(txn)

    if len(problematic) == 1:
        return problematic[0].id, "HEURISTIC", [problematic[0].id]

    disputed = []
    for txn in transactions:
        disputes = await ledger_service.get_disputes(session, txn.id)
        if any(d.status.value in {"OPEN", "UNDER_REVIEW"} for d in disputes):
            disputed.append(txn)
    if len(disputed) == 1:
        return disputed[0].id, "HEURISTIC", [disputed[0].id]

    candidates = [t.id for t in (problematic or disputed or transactions)][:5]
    if len(candidates) == 1:
        return candidates[0], "HEURISTIC", candidates
    return None, "AMBIGUOUS", candidates
