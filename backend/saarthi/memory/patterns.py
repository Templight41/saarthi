"""Recurring pattern intelligence — the third memory layer.

The first two layers answer "what is true now?" (Postgres) and "what does this
remind us of?" (pgvector). Neither answers "is this merchant's third settlement
delay this month?", which is the question that turns an incident into a
pattern.

Everything here is **counted, not inferred**. Each detector returns the
occurrences it found — a row, a timestamp, an id you can go and look at — and a
single shared rule turns a list of occurrences into a pattern. No model is
consulted, because a model asked to count will produce a plausible number
rather than a true one, and a merchant told "this is your third delay" deserves
that to be a fact.

Patterns are computed on demand rather than stored. A patterns table would be a
second place where a number about the ledger lives, and it would go stale the
moment a settlement completed. The cost is a handful of indexed queries; the
benefit is that a pattern cannot disagree with the rows it came from.

They remain **advisory**. A pattern reaches the diagnosing model as context and
the dashboard as history. It is never an input to the policy engine: how often
this has happened before does not change what Saarthi is allowed to do about it
today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import UNSETTLED_SETTLEMENT, CaseStatus, PaymentStatus, RefundStatus, SettlementStatus
from ..database.models import (
    Case,
    Escalation,
    Refund,
    Settlement,
    Transaction,
)
from ..services import notification_service


class PatternType(StrEnum):
    SETTLEMENT_DELAY = "SETTLEMENT_DELAY"
    PAYMENT_FAILURE_BURST = "PAYMENT_FAILURE_BURST"
    REFUND_FAILURE = "REFUND_FAILURE"
    NOTIFICATION_MISMATCH = "NOTIFICATION_MISMATCH"
    REPEATED_ESCALATION = "REPEATED_ESCALATION"
    CASE_VOLUME_SPIKE = "CASE_VOLUME_SPIKE"


class PatternSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass(frozen=True)
class Occurrence:
    """One countable thing that happened, and where to go and check it."""

    at: datetime
    transaction_id: str | None = None
    case_id: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class PatternRule:
    """Why a count becomes a pattern. One row per detector, all in one table,
    so "would this have fired?" is answerable by reading rather than tracing."""

    type: PatternType
    window: timedelta
    threshold: int
    label: str
    attention: str


# Mirrors `settlement_delay_grace_seconds`. A settlement that lands a minute
# after its expected time is noise, not a delay, and counting it would make the
# number mean nothing.
SETTLEMENT_GRACE = timedelta(minutes=15)


RULES: dict[PatternType, PatternRule] = {
    PatternType.SETTLEMENT_DELAY: PatternRule(
        type=PatternType.SETTLEMENT_DELAY,
        window=timedelta(days=30),
        threshold=3,
        label="settlement delays",
        attention="Check this merchant's settlement schedule with the banking team.",
    ),
    PatternType.PAYMENT_FAILURE_BURST: PatternRule(
        type=PatternType.PAYMENT_FAILURE_BURST,
        window=timedelta(minutes=10),
        threshold=5,
        label="payment failures",
        attention="Likely a terminal or connectivity fault rather than individual payments.",
    ),
    PatternType.REFUND_FAILURE: PatternRule(
        type=PatternType.REFUND_FAILURE,
        window=timedelta(days=30),
        threshold=2,
        label="refund failures",
        attention="Refunds for this merchant are failing repeatedly; check the gateway route.",
    ),
    PatternType.NOTIFICATION_MISMATCH: PatternRule(
        type=PatternType.NOTIFICATION_MISMATCH,
        window=timedelta(days=30),
        threshold=2,
        label="payment notifications with no matching payment",
        attention="The merchant is being told about payments the ledger does not have.",
    ),
    PatternType.REPEATED_ESCALATION: PatternRule(
        type=PatternType.REPEATED_ESCALATION,
        window=timedelta(days=30),
        threshold=2,
        label="escalations for the same reason",
        attention="The same judgement keeps reaching a person; the policy may need revisiting.",
    ),
    PatternType.CASE_VOLUME_SPIKE: PatternRule(
        type=PatternType.CASE_VOLUME_SPIKE,
        window=timedelta(days=7),
        threshold=3,
        label="cases this week against this merchant's own baseline",
        attention="Something changed for this merchant recently. Find out what.",
    ),
}


@dataclass
class MerchantPattern:
    merchant_id: str
    pattern_type: PatternType
    event_count: int
    window_days: float
    threshold: int
    severity: PatternSeverity
    confidence: float
    summary: str
    recommended_attention: str
    first_seen: datetime
    last_seen: datetime
    related_cases: list[str] = field(default_factory=list)
    related_transactions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "merchant_id": self.merchant_id,
            "pattern_type": self.pattern_type.value,
            "event_count": self.event_count,
            "window_days": self.window_days,
            "threshold": self.threshold,
            "severity": self.severity.value,
            "confidence": round(self.confidence, 2),
            "summary": self.summary,
            "recommended_attention": self.recommended_attention,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "related_cases": self.related_cases,
            "related_transactions": self.related_transactions,
            # Said in the payload itself, because this travels to a prompt.
            "source": "counted_from_postgres",
            "authoritative": False,
        }


def _window_label(window: timedelta) -> str:
    if window < timedelta(hours=1):
        return f"{int(window.total_seconds() // 60)} minutes"
    if window < timedelta(days=1):
        return f"{int(window.total_seconds() // 3600)} hours"
    return f"{int(window.days)} days"


def _build(
    rule: PatternRule,
    merchant_id: str,
    occurrences: list[Occurrence],
    *,
    severity: PatternSeverity | None = None,
    summary: str | None = None,
) -> MerchantPattern | None:
    """The one place a count becomes a pattern.

    `confidence` is strength of evidence, not a probability: how far past the
    threshold the count is. Three delays against a threshold of three is a
    pattern worth mentioning; six is one worth acting on. Nothing here is
    estimating a likelihood, and it should not be read as one.
    """
    count = len(occurrences)
    if count < rule.threshold:
        return None

    ordered = sorted(occurrences, key=lambda o: o.at)
    if severity is None:
        severity = PatternSeverity.HIGH if count >= rule.threshold * 2 else PatternSeverity.MEDIUM

    return MerchantPattern(
        merchant_id=merchant_id,
        pattern_type=rule.type,
        event_count=count,
        window_days=round(rule.window.total_seconds() / 86400, 4),
        threshold=rule.threshold,
        severity=severity,
        confidence=min(1.0, count / (rule.threshold * 2)),
        summary=summary or f"{count} {rule.label} in {_window_label(rule.window)}",
        recommended_attention=rule.attention,
        first_seen=ordered[0].at,
        last_seen=ordered[-1].at,
        related_cases=sorted({o.case_id for o in ordered if o.case_id}),
        related_transactions=sorted({o.transaction_id for o in ordered if o.transaction_id}),
    )


# --------------------------------------------------------------------------
# Detectors. Each one counts rows and returns what it counted.
# --------------------------------------------------------------------------
async def _settlement_delays(
    session: AsyncSession, merchant_id: str, since: datetime, now: datetime
) -> list[Occurrence]:
    """A settlement that missed its window, or a case diagnosed as a delay.

    Both are evidence of the same operational problem and they overlap, so they
    are unioned per transaction rather than counted twice.
    """
    found: dict[str, Occurrence] = {}

    rows = await session.execute(
        select(Settlement, Transaction)
        .join(Transaction, Settlement.transaction_id == Transaction.id)
        .where(Transaction.merchant_id == merchant_id)
    )
    for settlement, txn in rows:
        if settlement.expected_at is None:
            continue
        landed = settlement.completed_at
        deadline = settlement.expected_at + SETTLEMENT_GRACE
        if settlement.status == SettlementStatus.COMPLETED and landed is not None:
            late = landed > deadline
            at = landed
        elif settlement.status in {*UNSETTLED_SETTLEMENT, SettlementStatus.FAILED}:
            late = now > deadline
            at = settlement.expected_at
        else:
            continue
        if late and at >= since:
            found[txn.id] = Occurrence(at=at, transaction_id=txn.id, detail="settlement missed its window")

    cases = await session.scalars(
        select(Case).where(
            Case.merchant_id == merchant_id,
            Case.created_at >= since,
        )
    )
    for case in cases:
        root = (case.diagnosis or {}).get("root_cause")
        if root != "SETTLEMENT_DELAY":
            continue
        key = case.transaction_id or case.id
        found[key] = Occurrence(
            at=case.created_at,
            transaction_id=case.transaction_id,
            case_id=case.id,
            detail="case diagnosed as a settlement delay",
        )
    return list(found.values())


async def _payment_failure_burst(
    session: AsyncSession, merchant_id: str, window: timedelta, horizon: datetime
) -> list[Occurrence]:
    """The densest cluster of failures, not the total.

    Five failures spread over a month is a business; five in ten minutes is a
    broken terminal. Only the tightest window is returned, so the count means
    what the rule says it means.
    """
    rows = await session.scalars(
        select(Transaction)
        .where(
            Transaction.merchant_id == merchant_id,
            Transaction.payment_status == PaymentStatus.FAILED,
            Transaction.created_at >= horizon,
        )
        .order_by(Transaction.created_at)
    )
    failures = [
        Occurrence(at=t.created_at, transaction_id=t.id, detail="payment failed") for t in rows
    ]

    best: list[Occurrence] = []
    start = 0
    for end in range(len(failures)):
        while failures[end].at - failures[start].at > window:
            start += 1
        if end - start + 1 > len(best):
            best = failures[start : end + 1]
    return best


async def _refund_failures(
    session: AsyncSession, merchant_id: str, since: datetime
) -> list[Occurrence]:
    """A failed refund, or one that only succeeded on a retry."""
    rows = await session.execute(
        select(Refund, Transaction)
        .join(Transaction, Refund.transaction_id == Transaction.id)
        .where(Transaction.merchant_id == merchant_id, Refund.created_at >= since)
    )
    out: list[Occurrence] = []
    for refund, _txn in rows:
        failed = refund.status == RefundStatus.FAILED
        retried = refund.attempt_count > 1
        if not (failed or retried):
            continue
        out.append(
            Occurrence(
                at=refund.created_at,
                transaction_id=refund.transaction_id,
                case_id=refund.case_id,
                detail="refund failed" if failed else "refund needed a retry",
            )
        )
    return out


async def _notification_mismatches(
    session: AsyncSession, merchant_id: str, since: datetime
) -> list[Occurrence]:
    """A payment the merchant was told about that the ledger cannot confirm.

    The Soundbox announces; the ledger decides. Reconciliation is asked rather
    than reimplemented, so this counts exactly what Scenario 4 investigates.
    """
    results = await notification_service.reconcile_recent(
        session, merchant_id, since=since, limit=100
    )
    return [
        Occurrence(
            at=r.announced_at,
            transaction_id=r.transaction_id,
            detail=r.explanation,
        )
        for r in results
        if not r.confirmed
    ]


async def _repeated_escalations(
    session: AsyncSession, merchant_id: str, since: datetime
) -> list[Occurrence]:
    """Grouped by reason: the same judgement reaching a person repeatedly is
    the signal, not escalation volume as such."""
    rows = await session.execute(
        select(Escalation, Case)
        .join(Case, Escalation.case_id == Case.id)
        .where(Case.merchant_id == merchant_id, Escalation.created_at >= since)
    )
    by_reason: dict[str, list[Occurrence]] = {}
    for escalation, case in rows:
        by_reason.setdefault(escalation.reason, []).append(
            Occurrence(
                at=escalation.created_at,
                transaction_id=case.transaction_id,
                case_id=case.id,
                detail=escalation.reason,
            )
        )
    if not by_reason:
        return []
    return max(by_reason.values(), key=len)


async def _case_volume(
    session: AsyncSession, merchant_id: str, window: timedelta, now: datetime
) -> tuple[list[Occurrence], float]:
    """Recent cases, and this merchant's own baseline rate over the prior month.

    Compared against themselves rather than against other merchants: a busy
    merchant with steady volume is not an anomaly, and a quiet one doubling is.
    """
    recent_since = now - window
    baseline_since = now - (window * 5)

    rows = await session.scalars(
        select(Case).where(Case.merchant_id == merchant_id, Case.created_at >= baseline_since)
    )
    recent: list[Occurrence] = []
    older = 0
    for case in rows:
        if case.created_at >= recent_since:
            recent.append(
                Occurrence(at=case.created_at, case_id=case.id, transaction_id=case.transaction_id)
            )
        else:
            older += 1
    # Four prior windows make up the baseline.
    return recent, older / 4.0


# --------------------------------------------------------------------------
async def detect_patterns(
    session: AsyncSession,
    merchant_id: str,
    *,
    now: datetime | None = None,
    rules: dict[PatternType, PatternRule] | None = None,
) -> list[MerchantPattern]:
    """Every pattern this merchant currently meets the threshold for."""
    now = now or utcnow()
    rules = rules or RULES
    found: list[MerchantPattern] = []

    def rule(kind: PatternType) -> PatternRule | None:
        return rules.get(kind)

    if (r := rule(PatternType.SETTLEMENT_DELAY)) is not None:
        found.append(
            _build(r, merchant_id, await _settlement_delays(session, merchant_id, now - r.window, now))
        )

    if (r := rule(PatternType.PAYMENT_FAILURE_BURST)) is not None:
        # Look back a month for a cluster that is itself only minutes wide.
        occurrences = await _payment_failure_burst(
            session, merchant_id, r.window, now - timedelta(days=30)
        )
        found.append(_build(r, merchant_id, occurrences))

    if (r := rule(PatternType.REFUND_FAILURE)) is not None:
        found.append(
            _build(r, merchant_id, await _refund_failures(session, merchant_id, now - r.window))
        )

    if (r := rule(PatternType.NOTIFICATION_MISMATCH)) is not None:
        found.append(
            _build(r, merchant_id, await _notification_mismatches(session, merchant_id, now - r.window))
        )

    if (r := rule(PatternType.REPEATED_ESCALATION)) is not None:
        occurrences = await _repeated_escalations(session, merchant_id, now - r.window)
        reason = occurrences[0].detail if occurrences else ""
        found.append(
            _build(
                r,
                merchant_id,
                occurrences,
                summary=(
                    f"{len(occurrences)} escalations for {reason.replace('_', ' ').lower()} "
                    f"in {_window_label(r.window)}"
                )
                if occurrences
                else None,
            )
        )

    if (r := rule(PatternType.CASE_VOLUME_SPIKE)) is not None:
        recent, baseline = await _case_volume(session, merchant_id, r.window, now)
        # A spike needs both an absolute floor and a rise against the merchant's
        # own history, or every first week of activity looks like an anomaly.
        if len(recent) >= r.threshold and len(recent) >= max(baseline * 2, 1):
            severity = (
                PatternSeverity.LOW
                if baseline == 0
                else (
                    PatternSeverity.HIGH
                    if len(recent) >= baseline * 3
                    else PatternSeverity.MEDIUM
                )
            )
            found.append(
                _build(
                    r,
                    merchant_id,
                    recent,
                    severity=severity,
                    summary=(
                        f"{len(recent)} cases in {_window_label(r.window)}, against a baseline of "
                        f"{baseline:.1f}"
                    ),
                )
            )

    ranked = [p for p in found if p is not None]
    order = {PatternSeverity.HIGH: 0, PatternSeverity.MEDIUM: 1, PatternSeverity.LOW: 2}
    ranked.sort(key=lambda p: (order[p.severity], -p.event_count))
    return ranked


async def merchant_profile(
    session: AsyncSession, merchant_id: str, *, now: datetime | None = None
) -> dict:
    """What it has been like to be this merchant lately.

    Counted from Postgres, every number traceable to rows. Presented as
    operational history, never as current payment state.
    """
    now = now or utcnow()
    patterns = await detect_patterns(session, merchant_id, now=now)

    cases = list(
        await session.scalars(select(Case).where(Case.merchant_id == merchant_id))
    )
    resolved = [c for c in cases if c.status == CaseStatus.RESOLVED]
    autonomous = [c for c in resolved if c.resolution and c.resolution.value == "AUTONOMOUS"]
    escalated = [c for c in cases if c.status == CaseStatus.ESCALATED or c.requires_human]

    durations = [
        (c.resolved_at - c.created_at).total_seconds()
        for c in resolved
        if c.resolved_at and c.created_at
    ]

    by_intent: dict[str, int] = {}
    for case in cases:
        if case.intent:
            by_intent[case.intent] = by_intent.get(case.intent, 0) + 1

    return {
        "merchant_id": merchant_id,
        "generated_at": now.isoformat(),
        "total_cases": len(cases),
        "resolved_cases": len(resolved),
        "autonomous_resolutions": len(autonomous),
        "needed_a_person": len(escalated),
        "by_intent": by_intent,
        "median_resolution_seconds": _median(durations),
        "patterns": [p.as_dict() for p in patterns],
        "headlines": [p.summary for p in patterns],
        "source": "counted_from_postgres",
        "authoritative": False,
    }


def _median(values: list[float]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[mid])
    return int((ordered[mid - 1] + ordered[mid]) / 2)
