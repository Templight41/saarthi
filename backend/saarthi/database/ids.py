"""Human-readable sequential IDs.

The case counter starts at 18293 so the first case after a reset is
CASE-18293, matching the IDs used throughout the product specification.
Resetting the counters makes the demo byte-for-byte repeatable.
"""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Counter

SEEDS: dict[str, tuple[str, int]] = {
    # kind: (prefix, starting value - 1)
    "case": ("CASE-", 18292),
    "event": ("EVT-", 999),
    "action": ("ACT-", 499),
    "refund": ("RF-", 9000),
    "ticket": ("TKT-", 4000),
    "escalation": ("ESC-", 300),
    "message": ("MSG-", 7000),
    "dispute": ("DSP-", 600),
    "settlement": ("STL-", 2000),
    "transaction": ("TXN", 19999),
    "memory": ("MEM-", 100),
    "workflow": ("WF-", 200),
    "alert": ("ALERT-", 50),
}


async def reset_counters(session: AsyncSession) -> None:
    for kind, (_prefix, start) in SEEDS.items():
        existing = await session.get(Counter, kind)
        if existing is None:
            session.add(Counter(name=kind, value=start))
        else:
            existing.value = start
    await session.flush()


async def next_id(session: AsyncSession, kind: str) -> str:
    prefix, start = SEEDS[kind]
    result = await session.execute(
        update(Counter).where(Counter.name == kind).values(value=Counter.value + 1).returning(Counter.value)
    )
    row = result.scalar_one_or_none()
    if row is None:
        session.add(Counter(name=kind, value=start + 1))
        await session.flush()
        row = start + 1
    return f"{prefix}{row}"


async def peek_counter(session: AsyncSession, kind: str) -> int:
    value = await session.scalar(select(Counter.value).where(Counter.name == kind))
    return value if value is not None else SEEDS[kind][1]
