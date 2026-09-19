"""The Scenario Lab, and the five scenarios it exists to run.

These are acceptance tests written from the judge's side of the laptop: press
one button, watch the whole thing, and check afterwards that what the scenario
promised is what the audit trail says happened. Nothing here asserts on prose.
"""

from __future__ import annotations

import pytest

from saarthi.simulation.scenarios import SCENARIOS, ScenarioTrigger, resolve

pytestmark = pytest.mark.asyncio

CANONICAL = [
    "settlement_delay",
    "refund_failure",
    "high_value_dispute",
    "soundbox_mismatch",
    "proactive_anomaly",
]


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------
def test_there_are_exactly_five_canonical_scenarios():
    assert list(SCENARIOS) == CANONICAL


def test_every_scenario_says_where_its_authority_stops():
    for scenario in SCENARIOS.values():
        assert scenario.autonomy_boundary.strip(), scenario.id
        assert scenario.expected_outcome.strip(), scenario.id
        assert scenario.initial_state, scenario.id
        assert scenario.expected_behaviour, scenario.id
        assert scenario.checkpoints, scenario.id
        assert scenario.demo_steps, scenario.id


def test_a_checkpoint_is_always_something_observable():
    """No checkpoint may be satisfiable by opinion."""
    for scenario in SCENARIOS.values():
        for checkpoint in scenario.checkpoints:
            assert checkpoint.event_type or checkpoint.case_status, (
                f"{scenario.id}: '{checkpoint.label}' has nothing to read it off"
            )


def test_only_the_proactive_scenario_starts_without_a_merchant():
    for scenario in SCENARIOS.values():
        if scenario.trigger is ScenarioTrigger.MONITOR:
            assert scenario.message is None, scenario.id
        else:
            assert scenario.message, scenario.id


def test_familiar_short_keys_still_resolve():
    assert resolve("A").id == "settlement_delay"
    assert resolve("b").id == "refund_failure"
    assert resolve("C").id == "high_value_dispute"
    assert resolve("soundbox-mismatch").id == "soundbox_mismatch"
    # The two new scenarios have no letter, so muscle memory cannot silently
    # run something other than what the presenter meant.
    assert resolve("D") is None
    assert resolve("E") is None


async def test_the_catalogue_is_served(client):
    body = (await client.get("/api/scenarios")).json()
    assert [s["id"] for s in body["scenarios"]] == CANONICAL

    one = (await client.get("/api/scenarios/soundbox_mismatch")).json()
    assert one["capability"]
    assert one["trigger"] == "MERCHANT_MESSAGE"
    assert any("never heard of it" in line for line in one["initial_state"])


async def test_an_unknown_scenario_names_the_real_ones(client):
    response = await client.get("/api/scenarios/nonsense")
    assert response.status_code == 404
    assert "settlement_delay" in response.json()["detail"]


# --------------------------------------------------------------------------
# Reset and run
# --------------------------------------------------------------------------
async def test_reset_arms_without_starting_anything(client):
    body = (await client.post("/api/scenarios/refund_failure/reset")).json()
    assert body["case_id"] is None

    status = (await client.get("/api/scenarios/refund_failure/status")).json()
    assert status["phase"] == "NOT_STARTED"
    assert status["armed"] is True
    assert all(c["reached"] is False for c in status["checkpoints"])


async def test_reset_is_deterministic(client):
    """Twice in a row must give the same world, or the demo is not repeatable."""
    first = (await client.post("/api/scenarios/settlement_delay/run")).json()
    second = (await client.post("/api/scenarios/settlement_delay/run")).json()
    assert first["case_id"] == second["case_id"] == "CASE-18293"


async def _run(client, scenario_id: str) -> dict:
    started = (await client.post(f"/api/scenarios/{scenario_id}/run")).json()
    status = (await client.get(f"/api/scenarios/{scenario_id}/status")).json()
    return {**started, "status": status}


def _reached(status: dict, label_fragment: str) -> bool:
    return any(
        c["reached"] for c in status["checkpoints"] if label_fragment.lower() in c["label"].lower()
    )


# --------------------------------------------------------------------------
# Scenario 1 — settlement delay
# --------------------------------------------------------------------------
async def test_settlement_delay_parks_rather_than_concluding(client):
    run = await _run(client, "settlement_delay")
    status = run["status"]

    assert status["case_status"] not in {"RESOLVED", "ESCALATED"}
    assert _reached(status, "Standby refund scheduled")
    assert _reached(status, "Waiting on settlement")
    # It has not resolved anything, because it does not know yet.
    assert not _reached(status, "Resolved")


async def test_settlement_delay_resolves_once_settlement_lands(client):
    run = await _run(client, "settlement_delay")
    await client.post("/api/simulation/settlement/TXN18293/complete")

    status = (await client.get("/api/scenarios/settlement_delay/status")).json()
    assert status["case_status"] == "RESOLVED"
    assert status["resolution"] == "AUTONOMOUS"
    assert _reached(status, "Settlement verified")
    assert run["case_id"] == status["case_id"]


# --------------------------------------------------------------------------
# Scenario 2 — refund failure and recovery
# --------------------------------------------------------------------------
async def test_refund_failure_recovers_without_duplicating(client):
    run = await _run(client, "refund_failure")
    status = run["status"]

    assert status["case_status"] == "RESOLVED"
    assert _reached(status, "Attempt failed")
    assert _reached(status, "Checked whether the refund landed")
    assert _reached(status, "Retried on the same key")

    context = (await client.get(f"/api/cases/{run['case_id']}/context")).json()
    completed = [r for r in context["refunds"] if r["status"] == "COMPLETED"]
    assert len(completed) == 1, "a retry must not create a second refund"


# --------------------------------------------------------------------------
# Scenario 3 — high-value subjective dispute
# --------------------------------------------------------------------------
async def test_high_value_dispute_stops_and_hands_over(client):
    run = await _run(client, "high_value_dispute")
    status = run["status"]

    assert status["case_status"] == "ESCALATED"
    assert status["human_required_by_policy"] is True
    assert _reached(status, "Escalated with evidence")

    context = (await client.get(f"/api/cases/{run['case_id']}/context")).json()
    assert context["refunds"] == [], "nothing may be refunded before a person decides"


async def test_high_value_dispute_verifies_the_approved_refund(client):
    run = await _run(client, "high_value_dispute")
    escalations = (await client.get("/api/escalations?status=PENDING_HUMAN")).json()["escalations"]
    assert escalations

    await client.post(
        f"/api/escalations/{escalations[0]['id']}/approve",
        json={"decided_by": "ops@urbanthreads.in", "note": "goodwill"},
    )

    status = (await client.get("/api/scenarios/high_value_dispute/status")).json()
    assert status["case_status"] == "RESOLVED"
    assert status["resolution"] == "HUMAN_APPROVED"
    # The approved refund goes through the same verifier as any other.
    assert _reached(status, "Refund verified after approval")
    assert run["case_id"] == status["case_id"]


# --------------------------------------------------------------------------
# Scenario 4 — Soundbox and payment mismatch
# --------------------------------------------------------------------------
async def test_soundbox_mismatch_refuses_to_invent_a_payment(client):
    run = await _run(client, "soundbox_mismatch")
    status = run["status"]

    assert status["case_status"] == "ESCALATED"
    assert _reached(status, "Refused to invent a payment")

    case = (await client.get(f"/api/cases/{run['case_id']}")).json()
    assert case["diagnosis"]["root_cause"] == "ANNOUNCEMENT_WITHOUT_PAYMENT"

    context = (await client.get(f"/api/cases/{run['case_id']}/context")).json()
    assert context["refunds"] == [], "there is no payment here to refund"


async def test_the_ledger_answers_each_announcement_separately(client):
    run = await _run(client, "soundbox_mismatch")
    context = (await client.get(f"/api/cases/{run['case_id']}/context")).json()

    outcomes = {n["reference"]: n["outcome"] for n in context["notifications"]}
    assert outcomes["TXN_SOUNDBOX_OK"] == "MATCHED_SUCCESS"
    assert outcomes["TXN_SOUNDBOX_PENDING"] == "MATCHED_PENDING"
    assert outcomes["TXN20455"] == "NO_AUTHORITATIVE_RECORD"
    # Three identical-sounding announcements, three different truths.
    assert all(n["authoritative"] is False for n in context["notifications"])


# --------------------------------------------------------------------------
# Scenario 5 — proactive anomaly
# --------------------------------------------------------------------------
async def test_the_proactive_case_has_no_merchant_message(client):
    run = await _run(client, "proactive_anomaly")
    assert run["case_id"], "the monitor should have opened a case"

    case = (await client.get(f"/api/cases/{run['case_id']}")).json()
    assert case["origin"] == "PROACTIVE"

    messages = (await client.get(f"/api/cases/{run['case_id']}/messages")).json()["messages"]
    inbound = [m for m in messages if m["direction"] == "INBOUND"]
    assert inbound == [], "nobody reported this"


async def test_the_proactive_case_runs_the_ordinary_pipeline(client):
    """No second agent: the same states, the same policy engine, the same trail."""
    run = await _run(client, "proactive_anomaly")
    events = (await client.get(f"/api/cases/{run['case_id']}/timeline")).json()["events"]
    types = {e["type"] for e in events}

    assert {"DIAGNOSIS_COMPLETE", "POLICY_CHECKED"} <= types
    assert _reached(run["status"], "Anomaly detected")


# --------------------------------------------------------------------------
# The lab does not leak into the agent
# --------------------------------------------------------------------------
def test_the_agent_cannot_see_which_scenario_is_running():
    """Arming shapes the world, never the reasoning."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "saarthi"
    offenders = [
        path.relative_to(root)
        for package in ("agent", "policy", "verification", "recovery", "escalation")
        for path in (root / package).rglob("*.py")
        if "scenarios" in path.read_text() or "SCENARIOS" in path.read_text()
    ]
    assert offenders == []
