"""Deliberate, controlled failure injection.

Nothing here fails randomly. A failure is armed explicitly, consumed once, and
reset by the simulation endpoints, so the demo is repeatable on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..database.enums import RefundStatus


@dataclass
class SimulationState:
    active_scenario: str | None = None
    # Number of upcoming refund attempts that should fail.
    fail_next_refund_attempts: int = 0
    # Lets tests produce "API said OK but the refund is still pending".
    refund_result_status: RefundStatus = RefundStatus.COMPLETED
    refund_attempts_seen: dict[str, int] = field(default_factory=dict)
    step_delay_seconds: float | None = None
    auto_settle_after_seconds: float | None = None

    def consume_refund_failure(self) -> bool:
        if self.fail_next_refund_attempts > 0:
            self.fail_next_refund_attempts -= 1
            return True
        return False

    def record_attempt(self, key: str) -> int:
        self.refund_attempts_seen[key] = self.refund_attempts_seen.get(key, 0) + 1
        return self.refund_attempts_seen[key]

    def reset(self) -> None:
        self.active_scenario = None
        self.fail_next_refund_attempts = 0
        self.refund_result_status = RefundStatus.COMPLETED
        self.refund_attempts_seen.clear()
        self.step_delay_seconds = None
        self.auto_settle_after_seconds = None

    def as_dict(self) -> dict:
        return {
            "active_scenario": self.active_scenario,
            "fail_next_refund_attempts": self.fail_next_refund_attempts,
            "refund_result_status": self.refund_result_status.value,
            "refund_attempts_seen": dict(self.refund_attempts_seen),
            "step_delay_seconds": self.step_delay_seconds,
            "auto_settle_after_seconds": self.auto_settle_after_seconds,
        }


simulation_state = SimulationState()
