"""Seed operational knowledge.

These are the procedures and policies a new support teammate would be handed.
They are searchable alongside resolved cases, so the agent can retrieve "how we
handle this" as well as "what happened last time".
"""

from __future__ import annotations

KNOWLEDGE_DOCS: list[dict] = [
    {
        "title": "Refund limits and autonomous authority",
        "tags": ["refund", "policy", "limit", "approval"],
        "body": (
            "Each merchant carries an autonomous refund limit, ₹5,000 by default. A refund at or "
            "below that limit on a valid transaction may be issued without human approval. Above "
            "it, a human must approve before any money moves. Never tell a merchant a refund has "
            "completed until the refund ledger has been re-read and reports COMPLETED; a gateway "
            "returning success is not evidence that the refund happened."
        ),
    },
    {
        "title": "Subjective disputes require human judgement",
        "tags": ["dispute", "product_quality", "policy", "approval"],
        "body": (
            "Product-quality and not-as-described disputes turn on judgement about goods we cannot "
            "inspect, so they always require human review regardless of amount. Collect the "
            "evidence first: photographs, the customer's description, the order history and "
            "whether this merchant has had similar disputes. Present that to the reviewer with a "
            "recommendation rather than asking them to start over."
        ),
    },
    {
        "title": "Settlement delay procedure",
        "tags": ["settlement", "delay", "payment_pending", "bank_confirmation"],
        "body": (
            "A payment showing PAYMENT_PENDING while the customer was debited is not a failed "
            "payment. It is usually a settlement delay. Check the settlement record and its ETA "
            "before drawing any conclusion. Common delay reasons are BANK_CONFIRMATION, NPCI_BATCH "
            "and WEEKEND_CUTOFF. Typical resolution is one to three hours. Schedule a standby "
            "refund conditional on settlement not completing by the deadline, tell the merchant "
            "the real position, and let the condition resolve itself."
        ),
    },
    {
        "title": "Refund execution and safe retry",
        "tags": ["refund", "retry", "idempotency", "recovery"],
        "body": (
            "Issue a refund, then verify it by reading the refund record back. If the refund call "
            "fails, do not retry blindly. First look up the refund by its idempotency key to find "
            "out whether it landed despite the error. Retry only when the lookup proves no refund "
            "exists, and reuse the same idempotency key so a duplicate cannot be created. Stop "
            "after three attempts and escalate with every error attached."
        ),
    },
    {
        "title": "Escalation guidelines",
        "tags": ["escalation", "human", "approval", "handover"],
        "body": (
            "Escalate when a human's judgement, authority or verification is genuinely needed: "
            "amounts above authority, subjective disputes, low diagnostic confidence, repeated "
            "technical failures, or an explicit request for a person. An escalation must carry the "
            "checks already completed, the policy position, the specific action awaiting approval "
            "and a recommendation. Never escalate with nothing more than an admission of defeat."
        ),
    },
    {
        "title": "Merchant communication guidance",
        "tags": ["communication", "message", "tone"],
        "body": (
            "Be short and factual. Say what was found, what is true now and what happens next. "
            "Never claim a refund or settlement has completed unless it has been verified against "
            "the ledger. When waiting, say what is being waited on and what will happen if it does "
            "not arrive. Use rupee amounts and reference the transaction by its identifier."
        ),
    },
    {
        "title": "Common failure modes",
        "tags": ["failure", "recovery", "gateway", "timeout"],
        "body": (
            "The refund gateway returns 500 UPSTREAM_TIMEOUT under load; this is transient and "
            "safe to retry once the refund state has been checked. A 409 INVALID_STATE means the "
            "transaction cannot be refunded in its current state and retrying will never help. "
            "Duplicate submissions are prevented by the idempotency key, which is derived from the "
            "case, transaction and amount rather than generated per attempt."
        ),
    },
]


async def seed_knowledge(session, memory) -> int:
    from sqlalchemy import func, select

    from ..database.models import MemoryDocument

    existing = await session.scalar(
        select(func.count()).select_from(MemoryDocument).where(MemoryDocument.kind == "knowledge")
    )
    if existing:
        return 0
    for doc in KNOWLEDGE_DOCS:
        await memory.add_knowledge(
            session, title=doc["title"], content=doc["body"], tags=doc["tags"]
        )
    return len(KNOWLEDGE_DOCS)
