"""Merchants, and the authority each one grants Saarthi.

The refund limit is the single number that decides how much money Saarthi may
move without asking a person, and it is genuinely per-merchant: a merchant of
five years doing steady volume is not the same risk as one onboarded last
week. So it has to be editable.

Three things follow from it being an *authority* rather than a fixture, and
they are why this is not a one-line setter:

* **Bounded.** `MAX_AUTONOMOUS_REFUND_LIMIT` is a ceiling no merchant can be
  pushed past from here. Without one, "set it high enough" quietly turns every
  escalation into an autonomous action, which is the one failure mode this
  whole system is built to prevent.
* **Audited.** Every change records who made it, when, from what, to what and
  why. A limit with no history cannot be reviewed after the fact.
* **Not the agent's to change.** Nothing in `agent/`, `tools/` or `policy/`
  can reach this. `tests/test_merchant_authority.py` walks those packages to
  keep it that way — an agent that can raise its own limit has no limit.

The policy engine reads `merchant.autonomous_refund_limit` live on every
decision, so a change here applies to the next case with nothing to invalidate.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.models import Merchant
from ..runtime import SaarthiRuntime
from .deps import get_runtime, get_session

router = APIRouter(prefix="/api/merchants", tags=["merchants"])

#: How many past changes to keep on the merchant. Enough to review a decision,
#: not so many that the row grows without bound.
HISTORY_KEPT = 10


class RefundLimitChange(BaseModel):
    limit: Decimal = Field(ge=0)
    changed_by: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=300)


def _serialise(merchant: Merchant) -> dict:
    history = (merchant.meta or {}).get("refund_limit_history") or []
    last = history[-1] if history else None
    return {
        "id": merchant.id,
        "name": merchant.name,
        "risk_level": merchant.risk_level.value,
        "autonomous_refund_limit": str(merchant.autonomous_refund_limit),
        "currency": merchant.currency,
        "language": merchant.language,
        "limit_changed_at": last["at"] if last else None,
        "limit_changed_by": last["changed_by"] if last else None,
        "limit_history": history,
    }


@router.get("")
async def list_merchants(
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    rows = await session.scalars(select(Merchant).order_by(Merchant.id))
    return {
        "merchants": [_serialise(m) for m in rows],
        "max_autonomous_refund_limit": str(runtime.settings.max_autonomous_refund_limit),
    }


@router.get("/{merchant_id}/profile")
async def merchant_profile_endpoint(
    merchant_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    """Operational history, counted from Postgres. Never current payment state."""
    from ..memory.patterns import merchant_profile

    merchant = await session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(404, f"Merchant {merchant_id} not found")
    return await merchant_profile(session, merchant_id)


@router.patch("/{merchant_id}/refund-limit")
async def set_refund_limit(
    merchant_id: str,
    payload: RefundLimitChange,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    """Change how much Saarthi may refund for this merchant without asking."""
    merchant = await session.get(Merchant, merchant_id)
    if merchant is None:
        raise HTTPException(404, f"Merchant {merchant_id} not found")

    ceiling = Decimal(str(runtime.settings.max_autonomous_refund_limit))
    try:
        limit = Decimal(payload.limit).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise HTTPException(422, "Not a usable amount") from exc
    if limit > ceiling:
        raise HTTPException(
            422,
            f"₹{limit:,.2f} is above the ceiling of ₹{ceiling:,.2f}. "
            "Raising it further is a configuration change, not a UI one.",
        )

    previous = Decimal(merchant.autonomous_refund_limit)
    if previous == limit:
        return {"merchant": _serialise(merchant), "changed": False}

    entry = {
        "at": utcnow().isoformat(),
        "from": str(previous),
        "to": str(limit),
        "changed_by": payload.changed_by,
        "reason": payload.reason,
    }
    history = list((merchant.meta or {}).get("refund_limit_history") or [])
    history.append(entry)

    merchant.autonomous_refund_limit = limit
    # A whole new dict: JSON columns are not mutation-tracked, so appending in
    # place would be silently discarded.
    merchant.meta = {
        **(merchant.meta or {}),
        "refund_limit_history": history[-HISTORY_KEPT:],
    }
    await session.commit()
    await session.refresh(merchant)
    return {"merchant": _serialise(merchant), "changed": True, "change": entry}
