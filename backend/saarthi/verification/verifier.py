"""Verification engine.

Every function here takes an identifier and re-reads current state through the
read services. None of them accept an action result. That is the entire point:
a case is resolved because the business state says so, not because a call
returned success.

Settlement verification can also report a *triggered condition*, which is how a
parked Scenario A case learns that it must replan.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.database import utcnow
from ..database.enums import MessageStatus, RefundStatus, SettlementStatus, TicketStatus
from ..schemas.agent import CheckResult, Plan, VerificationResult, VerificationStatus
from ..services import ledger_service, ops_service, refund_service

CONDITION_SETTLEMENT_COMPLETED = "SETTLEMENT_COMPLETED"
CONDITION_REFUND_CONDITION_MET = "REFUND_CONDITION_MET"


class Verifier:
    async def verify_refund_state(
        self,
        session: AsyncSession,
        *,
        refund_id: str | None = None,
        idempotency_key: str | None = None,
        expected: RefundStatus = RefundStatus.COMPLETED,
    ) -> CheckResult:
        refund = None
        if refund_id:
            try:
                refund = await refund_service.get_refund(session, refund_id)
            except Exception:  # noqa: BLE001
                refund = None
        if refund is None and idempotency_key:
            refund = await refund_service.find_refund_by_idempotency_key(session, idempotency_key)

        if refund is None:
            return CheckResult(
                kind="refund",
                status=VerificationStatus.FAILED,
                observed={"found": False},
                expected={"status": expected.value},
                message="No refund record exists for this operation",
            )

        observed = {"id": refund.id, "status": refund.status.value, "amount": str(refund.amount)}
        if refund.status == expected:
            return CheckResult(
                kind="refund",
                status=VerificationStatus.VERIFIED,
                observed=observed,
                expected={"status": expected.value},
                message=f"Refund {refund.id} confirmed {refund.status.value}",
            )
        if refund.status in {RefundStatus.PENDING, RefundStatus.PROCESSING}:
            return CheckResult(
                kind="refund",
                status=VerificationStatus.PENDING,
                observed=observed,
                expected={"status": expected.value},
                message=f"Refund {refund.id} is still {refund.status.value}; not resolving yet",
            )
        return CheckResult(
            kind="refund",
            status=VerificationStatus.FAILED,
            observed=observed,
            expected={"status": expected.value},
            message=f"Refund {refund.id} is {refund.status.value}, expected {expected.value}",
        )

    async def verify_settlement_state(
        self,
        session: AsyncSession,
        transaction_id: str,
        *,
        deadline: datetime | None = None,
        scheduled_refund_open: bool = False,
    ) -> CheckResult:
        settlement = await ledger_service.get_settlement(session, transaction_id)
        observed = {
            "status": settlement.status.value,
            "completed_at": settlement.completed_at.isoformat() if settlement.completed_at else None,
        }

        if settlement.status == SettlementStatus.COMPLETED:
            if scheduled_refund_open:
                return CheckResult(
                    kind="settlement",
                    status=VerificationStatus.CONDITION_TRIGGERED,
                    observed=observed,
                    expected={"status": "COMPLETED"},
                    message="Settlement completed; the standby refund is no longer needed",
                    condition=CONDITION_SETTLEMENT_COMPLETED,
                )
            return CheckResult(
                kind="settlement",
                status=VerificationStatus.VERIFIED,
                observed=observed,
                expected={"status": "COMPLETED"},
                message="Settlement confirmed complete",
            )

        if settlement.status == SettlementStatus.FAILED:
            return CheckResult(
                kind="settlement",
                status=VerificationStatus.CONDITION_TRIGGERED,
                observed=observed,
                expected={"status": "COMPLETED"},
                message="Settlement failed; the refund condition is now met",
                condition=CONDITION_REFUND_CONDITION_MET,
            )

        # Still pending.
        if deadline is not None and utcnow() >= deadline:
            return CheckResult(
                kind="settlement",
                status=VerificationStatus.CONDITION_TRIGGERED,
                observed=observed,
                expected={"status": "COMPLETED"},
                message="Settlement deadline passed; the refund condition is now met",
                condition=CONDITION_REFUND_CONDITION_MET,
            )
        return CheckResult(
            kind="settlement",
            status=VerificationStatus.PENDING,
            observed=observed,
            expected={"status": "COMPLETED"},
            message="Settlement is still pending within its expected window",
        )

    async def verify_transaction_state(
        self, session: AsyncSession, transaction_id: str, expected: dict
    ) -> CheckResult:
        txn = await ledger_service.get_transaction(session, transaction_id)
        observed = {
            "payment_status": txn.payment_status.value,
            "customer_debited": txn.customer_debited,
            "refunded_amount": str(txn.refunded_amount),
        }
        mismatches = [
            key for key, value in expected.items() if str(observed.get(key)) != str(value)
        ]
        return CheckResult(
            kind="transaction",
            status=VerificationStatus.VERIFIED if not mismatches else VerificationStatus.FAILED,
            observed=observed,
            expected=expected,
            message=(
                "Transaction state matches what the merchant was told"
                if not mismatches
                else f"Transaction state differs on {', '.join(mismatches)}"
            ),
        )

    async def verify_ticket_state(
        self, session: AsyncSession, ticket_id: str, expected: TicketStatus = TicketStatus.OPEN
    ) -> CheckResult:
        ticket = await ops_service.get_ticket(session, ticket_id)
        ok = ticket.status == expected
        return CheckResult(
            kind="ticket",
            status=VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
            observed={"id": ticket.id, "status": ticket.status.value},
            expected={"status": expected.value},
            message=f"Ticket {ticket.id} is {ticket.status.value}",
        )

    async def verify_message_status(
        self, session: AsyncSession, message_id: str, expected: MessageStatus = MessageStatus.SENT
    ) -> CheckResult:
        message = await ops_service.get_message(session, message_id)
        ok = message.status == expected
        return CheckResult(
            kind="message",
            status=VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
            observed={"id": message.id, "status": message.status.value},
            expected={"status": expected.value},
            message=f"Message {message.id} is {message.status.value}",
        )

    # ------------------------------------------------------------------
    async def verify_plan(
        self, session: AsyncSession, case, plan: Plan, *, outputs: dict[int, dict]
    ) -> VerificationResult:
        checks: list[CheckResult] = []

        for condition in plan.goal_conditions:
            check = await self._verify_condition(session, case, condition, outputs)
            if check is not None:
                checks.append(check)

        # Aggregate: a triggered condition takes precedence, then failure, then
        # pending. Only an unambiguous pass resolves the case.
        triggered = next((c for c in checks if c.status == VerificationStatus.CONDITION_TRIGGERED), None)
        if triggered is not None:
            return VerificationResult(
                status=VerificationStatus.CONDITION_TRIGGERED,
                checks=checks,
                condition=triggered.condition,
            )
        if any(c.status == VerificationStatus.FAILED for c in checks):
            return VerificationResult(status=VerificationStatus.FAILED, checks=checks)
        if any(c.status == VerificationStatus.PENDING for c in checks):
            return VerificationResult(status=VerificationStatus.PENDING, checks=checks)
        return VerificationResult(status=VerificationStatus.VERIFIED, checks=checks)

    async def _verify_condition(
        self, session: AsyncSession, case, condition, outputs: dict[int, dict]
    ) -> CheckResult | None:
        params = condition.params
        match condition.kind:
            case "REFUND_COMPLETED":
                refund_id = _find_output_id(outputs, ("issue_refund",))
                return await self.verify_refund_state(session, refund_id=refund_id)

            case "SETTLEMENT_COMPLETED":
                return await self.verify_settlement_state(session, params["transaction_id"])

            case "SETTLEMENT_RESOLVED_OR_REFUNDED":
                scheduled = await refund_service.find_scheduled_refund_for_case(session, case.id)
                deadline = None
                if scheduled is not None and scheduled.scheduled_for is not None:
                    deadline = scheduled.scheduled_for
                elif params.get("deadline"):
                    deadline = datetime.fromisoformat(params["deadline"])
                return await self.verify_settlement_state(
                    session,
                    params["transaction_id"],
                    deadline=deadline,
                    scheduled_refund_open=scheduled is not None,
                )

            case "SCHEDULED_REFUND_CANCELLED":
                refund_id = _find_output_id(outputs, ("cancel_scheduled_refund",))
                if refund_id is None:
                    return None
                return await self.verify_refund_state(
                    session, refund_id=refund_id, expected=RefundStatus.CANCELLED
                )

            case "MESSAGE_SENT":
                message_id = _find_output_id(outputs, ("send_message",))
                if message_id is None:
                    return CheckResult(
                        kind="message",
                        status=VerificationStatus.FAILED,
                        message="No message was sent",
                    )
                return await self.verify_message_status(session, message_id)

            case "TICKET_OPEN":
                ticket_id = _find_output_id(outputs, ("create_ticket",))
                if ticket_id is None:
                    return None
                return await self.verify_ticket_state(session, ticket_id)

            case "TRANSACTION_STATE_MATCHES":
                expected = {k: v for k, v in params.items() if k != "transaction_id"}
                return await self.verify_transaction_state(
                    session, params["transaction_id"], expected
                )

        return None


def _find_output_id(outputs: dict[int, dict], tools: tuple[str, ...]) -> str | None:
    for payload in reversed(list(outputs.values())):
        if payload.get("_tool") in tools and payload.get("id"):
            return payload["id"]
    return None


