"""Deterministic demo data.

Nothing here is random. Every scenario in the specification has fixed IDs,
amounts and states so the demo is repeatable, and historical resolved cases
give the memory panel and the metrics tiles something real to show before the
first live case is created.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import utcnow
from .enums import (
    ActionStatus,
    Actor,
    CaseOrigin,
    CaseOwner,
    CaseStatus,
    DisputeStatus,
    DisputeType,
    EventStatus,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    PaymentStatus,
    RefundStatus,
    Resolution,
    RiskLevel,
    SettlementStatus,
)
from .ids import reset_counters
from .models import (
    Action,
    AgentEvent,
    Case,
    Counter,
    Dispute,
    Escalation,
    MemoryDocument,
    Merchant,
    Message,
    Policy,
    ProactiveAlert,
    Refund,
    Settlement,
    Ticket,
    Transaction,
    TransactionEvent,
    WorkflowRun,
)

SEED_VERSION = "1.0.0"

# Delete order respects foreign keys.
_WIPE_ORDER = [
    ProactiveAlert,
    WorkflowRun,
    AgentEvent,
    Action,
    Message,
    Escalation,
    Ticket,
    Refund,
    Dispute,
    Settlement,
    TransactionEvent,
    Case,
    Transaction,
    Merchant,
    MemoryDocument,
    Policy,
    Counter,
]


async def wipe(session: AsyncSession) -> None:
    for model in _WIPE_ORDER:
        await session.execute(delete(model))
    await session.flush()


def _policies() -> list[Policy]:
    return [
        Policy(
            id="POL-REFUND-LIMIT",
            name="Autonomous refund limit",
            policy_type="REFUND_LIMIT",
            configuration={"default_limit": 5000, "use_merchant_limit": True, "currency": "INR"},
            priority=10,
        ),
        Policy(
            id="POL-SUBJECTIVE-DISPUTE",
            name="Subjective disputes require human judgement",
            policy_type="SUBJECTIVE_DISPUTE",
            configuration={
                "subjective_types": ["PRODUCT_QUALITY", "OTHER"],
                "subjective_intents": ["PRODUCT_QUALITY_DISPUTE"],
            },
            priority=20,
        ),
        Policy(
            id="POL-TXN-STATE",
            name="Transaction state validity",
            policy_type="TRANSACTION_STATE",
            configuration={
                "refundable_statuses": ["SUCCESS"],
                "pending_refund_requires_settlement": ["FAILED"],
                "schedule_allowed_statuses": ["PAYMENT_PENDING"],
            },
            priority=5,
        ),
        Policy(
            id="POL-PENDING-PAYMENT",
            name="Investigate settlement before declaring failure",
            policy_type="PENDING_PAYMENT",
            configuration={"grace_minutes": 30},
            priority=30,
        ),
        Policy(
            id="POL-RETRY",
            name="Safe retry of idempotent operations",
            policy_type="RETRY",
            configuration={"max_attempts": 3, "retryable_failure_classes": ["TRANSIENT_API_ERROR"]},
            priority=40,
        ),
        Policy(
            id="POL-SENSITIVE-ACCOUNT",
            name="Sensitive account changes require verification",
            policy_type="SENSITIVE_ACCOUNT",
            configuration={},
            priority=15,
        ),
    ]


def _historical_case(
    *,
    case_id: str,
    txn_id: str,
    merchant_id: str,
    amount: str,
    intent: str,
    root_cause: str,
    summary: str,
    outcome: str,
    days_ago: int,
    duration_seconds: int,
    now: datetime,
    settlement_window_minutes: int = 120,
) -> tuple[Transaction, Settlement, Case, list[AgentEvent], Action, Message]:
    created = now - timedelta(days=days_ago)
    resolved = created + timedelta(seconds=duration_seconds)

    txn = Transaction(
        id=txn_id,
        merchant_id=merchant_id,
        amount=Decimal(amount),
        payment_status=PaymentStatus.SUCCESS,
        customer_debited=True,
        customer_reference=f"CUST-{txn_id[-4:]}",
        description=summary,
        created_at=created,
        updated_at=resolved,
    )
    stl = Settlement(
        id=f"STL-{txn_id[-4:]}",
        transaction_id=txn_id,
        status=SettlementStatus.COMPLETED,
        # A case that was diagnosed as a settlement delay should have a
        # settlement that actually missed its window, or the fixtures disagree
        # with themselves and the pattern engine is right to ignore them.
        expected_at=created + timedelta(minutes=settlement_window_minutes),
        completed_at=resolved,
        updated_at=resolved,
    )
    case = Case(
        id=case_id,
        merchant_id=merchant_id,
        transaction_id=txn_id,
        intent=intent,
        status=CaseStatus.RESOLVED,
        origin=CaseOrigin.MERCHANT_CHAT,
        owner=CaseOwner.SAARTHI,
        diagnosis={"root_cause": root_cause, "confidence": 0.93, "summary": summary},
        confidence=0.93,
        risk=RiskLevel.LOW,
        requires_human=False,
        original_message=summary,
        resolution=Resolution.AUTONOMOUS,
        next_sequence=11,
        first_action_at=created + timedelta(seconds=6),
        created_at=created,
        updated_at=resolved,
        resolved_at=resolved,
    )

    event_specs = [
        ("CASE_CREATED", Actor.MERCHANT, "New merchant case received", 0),
        ("MERCHANT_IDENTIFIED", Actor.SAARTHI, "Merchant identified", 2),
        ("TRANSACTION_RETRIEVED", Actor.SAARTHI, f"Transaction {txn_id} retrieved", 4),
        ("SETTLEMENT_CHECKED", Actor.SAARTHI, "Settlement state checked", 6),
        ("DIAGNOSIS_COMPLETE", Actor.SAARTHI, f"Issue classified: {root_cause}", 9),
        ("POLICY_CHECKED", Actor.SAARTHI, "Action permitted under merchant policy", 11),
        # Every action row needs a matching started event, or audit coverage
        # correctly reports the trail as incomplete.
        ("ACTION_STARTED", Actor.SAARTHI, "Running send_message", 13),
        ("ACTION_COMPLETED", Actor.SAARTHI, "send_message completed", 15),
        ("ACTION_VERIFIED", Actor.SAARTHI, outcome, duration_seconds - 20),
        ("MESSAGE_SENT", Actor.SAARTHI, "Merchant notified", duration_seconds - 10),
        ("CASE_RESOLVED", Actor.SAARTHI, "Case resolved", duration_seconds),
    ]
    events = [
        AgentEvent(
            id=f"EVT-H{case_id[-5:]}{i}",
            case_id=case_id,
            event_type=etype,
            actor=actor,
            status=EventStatus.SUCCESS,
            message=msg,
            sequence=i,
            timestamp=created + timedelta(seconds=offset),
        )
        for i, (etype, actor, msg, offset) in enumerate(event_specs)
    ]

    action = Action(
        id=f"ACT-H{case_id[-5:]}",
        case_id=case_id,
        action_type="send_message",
        status=ActionStatus.COMPLETED,
        input={"case_id": case_id},
        result={"status": "SENT"},
        attempt=1,
        created_at=created + timedelta(seconds=6),
        completed_at=created + timedelta(seconds=8),
    )
    message = Message(
        id=f"MSG-H{case_id[-5:]}",
        case_id=case_id,
        direction=MessageDirection.OUTBOUND,
        channel=MessageChannel.CHAT,
        sender=Actor.SAARTHI,
        content=outcome,
        status=MessageStatus.SENT,
        meta={"stage": "OUTCOME", "claims": [], "language": "en-IN"},
        created_at=resolved,
    )
    return txn, stl, case, events, action, message


async def seed_all(session: AsyncSession, *, now: datetime | None = None) -> dict:
    """Wipe and reseed. Idempotent: safe to call on every reset."""
    now = now or utcnow()
    await wipe(session)
    await reset_counters(session)

    session.add_all(_policies())

    urban = Merchant(
        id="M1001",
        name="Urban Threads",
        email="ops@urbanthreads.in",
        phone="+91 98200 11001",
        risk_level=RiskLevel.LOW,
        autonomous_refund_limit=Decimal("5000.00"),
        language="en-IN",
        created_at=now - timedelta(days=420),
    )
    kaveri = Merchant(
        id="M1002",
        name="Kaveri Foods",
        email="support@kaverifoods.in",
        phone="+91 98200 11002",
        risk_level=RiskLevel.MEDIUM,
        autonomous_refund_limit=Decimal("2000.00"),
        # Kaveri is served in Hindi, which is what makes the proactive case
        # demonstrate the language path end to end.
        language="hi-IN",
        created_at=now - timedelta(days=200),
    )
    session.add_all([urban, kaveri])
    await session.flush()

    # ---- Scenario A: settlement delay -------------------------------------
    txn_a = Transaction(
        id="TXN18293",
        merchant_id="M1001",
        amount=Decimal("3200.00"),
        payment_status=PaymentStatus.PAYMENT_PENDING,
        customer_debited=True,
        customer_reference="CUST-8821",
        description="Order #A-6120",
        created_at=now - timedelta(minutes=45),
    )
    stl_a = Settlement(
        id="STL-2001",
        transaction_id="TXN18293",
        status=SettlementStatus.PENDING,
        expected_at=now + timedelta(hours=2, minutes=15),
        delay_reason="BANK_CONFIRMATION",
    )
    session.add_all([txn_a, stl_a])
    await session.flush()
    session.add_all(
        [
            TransactionEvent(
                transaction_id="TXN18293",
                kind="PAYMENT_INITIATED",
                detail={"amount": "3200.00"},
                occurred_at=now - timedelta(minutes=45),
            ),
            TransactionEvent(
                transaction_id="TXN18293",
                kind="CUSTOMER_DEBITED",
                detail={"amount": "3200.00", "confirmed": True},
                occurred_at=now - timedelta(minutes=44),
            ),
            TransactionEvent(
                transaction_id="TXN18293",
                kind="BANK_CONFIRMATION_PENDING",
                detail={"reason": "BANK_CONFIRMATION"},
                occurred_at=now - timedelta(minutes=43),
            ),
        ]
    )

    # ---- Scenario B: refund API failure ------------------------------------
    txn_b = Transaction(
        id="TXN_REFUND_FAILURE",
        merchant_id="M1001",
        amount=Decimal("2500.00"),
        payment_status=PaymentStatus.SUCCESS,
        customer_debited=True,
        customer_reference="CUST-5521",
        description="Order #A-5521, cancelled by customer",
        created_at=now - timedelta(days=1),
    )
    stl_b = Settlement(
        id="STL-2002",
        transaction_id="TXN_REFUND_FAILURE",
        status=SettlementStatus.COMPLETED,
        expected_at=now - timedelta(days=1, hours=-2),
        completed_at=now - timedelta(hours=22),
    )
    session.add_all([txn_b, stl_b])
    await session.flush()
    session.add(
        TransactionEvent(
            transaction_id="TXN_REFUND_FAILURE",
            kind="SETTLED",
            detail={"amount": "2500.00"},
            occurred_at=now - timedelta(hours=22),
        )
    )

    # ---- Scenario C: high-value subjective dispute -------------------------
    txn_c = Transaction(
        id="TXN_HIGH_VALUE_DISPUTE",
        merchant_id="M1001",
        amount=Decimal("15000.00"),
        payment_status=PaymentStatus.SUCCESS,
        customer_debited=True,
        customer_reference="CUST-7742",
        description="Order #A-4410, premium jacket",
        created_at=now - timedelta(days=3),
    )
    stl_c = Settlement(
        id="STL-2003",
        transaction_id="TXN_HIGH_VALUE_DISPUTE",
        status=SettlementStatus.COMPLETED,
        expected_at=now - timedelta(days=3, hours=-2),
        completed_at=now - timedelta(days=2, hours=22),
    )
    session.add_all([txn_c, stl_c])
    await session.flush()
    session.add(
        Dispute(
            id="DSP-601",
            transaction_id="TXN_HIGH_VALUE_DISPUTE",
            type=DisputeType.PRODUCT_QUALITY,
            description="Customer reports stitching defects and poor fabric quality.",
            requested_amount=Decimal("15000.00"),
            status=DisputeStatus.OPEN,
            evidence={"photos": 2, "customer_note": "stitching defects along the seam"},
            created_at=now - timedelta(days=1),
        )
    )

    # ---- Scenario D: normal successful transaction -------------------------
    txn_d = Transaction(
        id="TXN_NORMAL_SUCCESS",
        merchant_id="M1001",
        amount=Decimal("1200.00"),
        payment_status=PaymentStatus.SUCCESS,
        customer_debited=True,
        customer_reference="CUST-3310",
        description="Order #A-6008",
        created_at=now - timedelta(hours=4),
    )
    stl_d = Settlement(
        id="STL-2004",
        transaction_id="TXN_NORMAL_SUCCESS",
        status=SettlementStatus.COMPLETED,
        # Comfortably inside its window: this is the transaction where nothing
        # went wrong, so nothing about it should read as late.
        expected_at=now - timedelta(hours=1, minutes=30),
        completed_at=now - timedelta(hours=2),
    )
    session.add_all([txn_d, stl_d])

    # ---- Proactive monitor target, disarmed until the demo triggers it -----
    txn_p = Transaction(
        id="TXN19931",
        merchant_id="M1001",
        amount=Decimal("4800.00"),
        payment_status=PaymentStatus.PAYMENT_PENDING,
        customer_debited=True,
        customer_reference="CUST-9931",
        description="Order #A-6141",
        created_at=now - timedelta(hours=4),
    )
    stl_p = Settlement(
        id="STL-2005",
        transaction_id="TXN19931",
        status=SettlementStatus.PENDING,
        expected_at=now - timedelta(hours=2),
        delay_reason="NPCI_BATCH",
        monitor_armed=False,
    )
    session.add_all([txn_p, stl_p])
    await session.flush()

    # ---- Historical resolved cases ----------------------------------------
    historical = [
        _historical_case(
            case_id="CASE-17421",
            txn_id="TXN17421",
            merchant_id="M1001",
            amount="2900.00",
            intent="PAYMENT_DEBITED_BUT_NOT_CONFIRMED",
            root_cause="SETTLEMENT_DELAY",
            summary="My customer paid but it shows failed and the money was deducted.",
            outcome="Settlement completed after 1h 47m; no refund was needed.",
            days_ago=9,
            duration_seconds=6420,
            now=now,
            settlement_window_minutes=30,
        ),
        _historical_case(
            case_id="CASE-16842",
            txn_id="TXN16842",
            merchant_id="M1001",
            amount="1750.00",
            intent="PAYMENT_DEBITED_BUT_NOT_CONFIRMED",
            root_cause="SETTLEMENT_DELAY",
            summary="Payment shows pending for my customer but money left their account.",
            outcome="Settlement completed after 58m; merchant informed.",
            days_ago=21,
            duration_seconds=3480,
            now=now,
            settlement_window_minutes=30,
        ),
        _historical_case(
            case_id="CASE-16210",
            txn_id="TXN16210",
            merchant_id="M1001",
            amount="2100.00",
            intent="SETTLEMENT_DELAY",
            root_cause="SETTLEMENT_DELAY",
            summary="Settlement has not arrived for yesterday's order.",
            outcome="Settlement failed; scheduled refund executed and verified.",
            days_ago=40,
            duration_seconds=11100,
            now=now,
            settlement_window_minutes=30,
        ),
        _historical_case(
            case_id="CASE-17788",
            txn_id="TXN17788",
            merchant_id="M1001",
            amount="3400.00",
            intent="SETTLEMENT_DELAY",
            root_cause="SETTLEMENT_DELAY",
            summary="Yesterday's settlement is still not in my account.",
            outcome="Settlement completed after 2h 12m; merchant informed.",
            days_ago=4,
            duration_seconds=7920,
            now=now,
            settlement_window_minutes=30,
        ),
        _historical_case(
            case_id="CASE-15977",
            txn_id="TXN15977",
            merchant_id="M1002",
            amount="1400.00",
            intent="REFUND_REQUEST",
            root_cause="CUSTOMER_REFUND_REQUEST",
            summary="Customer cancelled, please refund.",
            outcome="Refund gateway failed once, retried safely and verified completed.",
            days_ago=14,
            duration_seconds=900,
            now=now,
        ),
    ]
    # ---- Operational history with no case attached ------------------------
    # Not every problem becomes a support case. These rows exist so the pattern
    # engine has something true to count that the case list does not already
    # show: a terminal that dropped five payments in six minutes, and refunds
    # that keep needing a second attempt.
    burst_at = now - timedelta(days=3)
    for index in range(5):
        session.add(
            Transaction(
                id=f"TXN17{600 + index}",
                merchant_id="M1002",
                amount=Decimal("450.00"),
                payment_status=PaymentStatus.FAILED,
                customer_debited=False,
                customer_reference=f"CUST-71{index}0",
                description="Counter sale",
                created_at=burst_at + timedelta(minutes=index + index // 2),
                updated_at=burst_at + timedelta(minutes=index + index // 2),
            )
        )
    await session.flush()

    for index, (suffix, days, status, attempts) in enumerate(
        [
            ("17610", 3, RefundStatus.FAILED, 1),
            ("17611", 2, RefundStatus.COMPLETED, 2),
        ]
    ):
        created = now - timedelta(days=days)
        txn_id = f"TXN{suffix}"
        # A refund needs a payment that actually succeeded to refund.
        session.add(
            Transaction(
                id=txn_id,
                merchant_id="M1002",
                amount=Decimal("450.00"),
                payment_status=PaymentStatus.SUCCESS,
                customer_debited=True,
                customer_reference=f"CUST-{suffix}",
                description="Counter sale",
                created_at=created - timedelta(hours=2),
                updated_at=created,
            )
        )
        await session.flush()
        session.add(
            Refund(
                id=f"RFD-H{index}",
                transaction_id=txn_id,
                amount=Decimal("450.00"),
                status=status,
                reason="Customer cancelled",
                attempt_count=attempts,
                idempotency_key=f"refund:history:{txn_id}:450.00",
                completed_at=created if status == RefundStatus.COMPLETED else None,
                created_at=created,
                updated_at=created,
            )
        )
    await session.flush()

    for txn, stl, case, events, action, message in historical:
        session.add(txn)
        await session.flush()
        session.add_all([stl, case])
        await session.flush()
        session.add_all([*events, action, message])

    await session.flush()
    await session.commit()

    return {
        "seed_version": SEED_VERSION,
        "merchants": 2,
        "transactions": await session.scalar(
            select(func.count()).select_from(Transaction)
        ),
        "historical_cases": len(historical),
        "policies": 6,
        "seeded_at": now.astimezone(UTC).isoformat(),
    }
