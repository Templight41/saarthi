"""API surface tests, driving the same flows a dashboard would."""

from __future__ import annotations

import pytest

from saarthi.simulation.failure_injection import simulation_state

pytestmark = pytest.mark.asyncio


async def test_create_case_returns_snapshot(client):
    response = await client.post(
        "/api/cases",
        json={
            "merchant_id": "M1001",
            "message": "My customer's payment failed, but the money was deducted.",
            "transaction_id": "TXN18293",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["id"].startswith("CASE-")
    assert body["status"] == "VERIFYING"
    assert body["intent"] == "PAYMENT_DEBITED_BUT_NOT_CONFIRMED"
    assert body["wait_reason"] == "SETTLEMENT_PENDING"


async def test_timeline_is_ordered_and_machine_readable(client):
    created = await client.post(
        "/api/cases",
        json={
            "message": "My customer's payment failed, but the money was deducted.",
            "transaction_id": "TXN18293",
        },
    )
    case_id = created.json()["id"]

    response = await client.get(f"/api/cases/{case_id}/timeline")
    assert response.status_code == 200
    events = response.json()["events"]
    assert events
    assert [e["sequence"] for e in events] == sorted(e["sequence"] for e in events)
    for event in events:
        assert event["type"]
        assert event["status"]
        assert event["message"]


async def test_context_includes_state_and_memory(client):
    created = await client.post(
        "/api/cases",
        json={
            "message": "My customer's payment failed, but the money was deducted.",
            "transaction_id": "TXN18293",
        },
    )
    case_id = created.json()["id"]

    body = (await client.get(f"/api/cases/{case_id}/context")).json()
    assert body["transaction"]["id"] == "TXN18293"
    assert body["settlement"]["status"] in {"PENDING", "COMPLETED"}
    assert body["merchant"]["autonomous_refund_limit"] == "5000.00"
    assert body["memory"]["similar_cases"], "memory panel needs prior cases"
    assert body["memory"]["merchant_history"]["previous_case_count"] >= 3


async def test_escalation_approve_flow(client):
    created = await client.post(
        "/api/cases",
        json={
            "message": "The customer says the product quality was poor and wants a ₹15,000 partial refund.",
            "transaction_id": "TXN_HIGH_VALUE_DISPUTE",
        },
    )
    case_id = created.json()["id"]

    listing = (await client.get("/api/escalations", params={"status": "PENDING_HUMAN"})).json()
    assert listing["escalations"]
    escalation = listing["escalations"][0]
    assert escalation["recommendation"] == "REVIEW_PARTIAL_REFUND"
    assert escalation["completed_actions"]
    assert escalation["pending_action"]["tool"] == "issue_refund"

    approved = await client.post(
        f"/api/escalations/{escalation['id']}/approve", json={"decided_by": "ops@urbanthreads.in"}
    )
    assert approved.status_code == 202

    case = (await client.get(f"/api/cases/{case_id}")).json()
    assert case["status"] == "RESOLVED"
    assert case["resolution"] == "HUMAN_APPROVED"

    # A second decision on a settled escalation is a conflict, not a repeat.
    again = await client.post(
        f"/api/escalations/{escalation['id']}/approve", json={"decided_by": "ops@urbanthreads.in"}
    )
    assert again.status_code == 409


async def test_escalation_reject_flow(client):
    await client.post(
        "/api/cases",
        json={
            "message": "The customer says the product quality was poor and wants a ₹15,000 partial refund.",
            "transaction_id": "TXN_HIGH_VALUE_DISPUTE",
        },
    )
    escalation = (await client.get("/api/escalations")).json()["escalations"][0]
    response = await client.post(
        f"/api/escalations/{escalation['id']}/reject",
        json={"decided_by": "ops@urbanthreads.in", "note": "Evidence is insufficient."},
    )
    assert response.status_code == 202

    case = (await client.get(f"/api/cases/{escalation['case_id']}")).json()
    assert case["status"] == "RESOLVED"
    assert case["resolution"] == "HUMAN_REJECTED"


async def test_takeover_stops_the_agent(client):
    await client.post(
        "/api/cases",
        json={
            "message": "The customer says the product quality was poor and wants a ₹15,000 partial refund.",
            "transaction_id": "TXN_HIGH_VALUE_DISPUTE",
        },
    )
    escalation = (await client.get("/api/escalations")).json()["escalations"][0]
    response = await client.post(
        f"/api/escalations/{escalation['id']}/takeover", json={"assigned_to": "ops@urbanthreads.in"}
    )
    assert response.status_code == 202

    case = (await client.get(f"/api/cases/{escalation['case_id']}")).json()
    assert case["owner"] == "HUMAN"


async def test_simulation_reset_is_repeatable(client):
    first = await client.post("/api/simulation/reset")
    assert first.status_code == 200
    created = await client.post(
        "/api/cases", json={"message": "hello", "transaction_id": "TXN_NORMAL_SUCCESS"}
    )
    # Counters reset, so the first case after a reset is always the same id.
    assert created.json()["id"] == "CASE-18293"


async def test_scenario_endpoint_arms_failure(client):
    response = await client.post("/api/simulation/scenario/B")
    assert response.status_code == 200
    body = response.json()
    assert body["transaction_id"] == "TXN_REFUND_FAILURE"
    assert body["simulation"]["fail_next_refund_attempts"] == 1
    assert "₹2,500" in body["suggested_message"]


async def test_metrics_shape_and_simulated_flag(client):
    await client.post(
        "/api/cases",
        json={
            "message": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
            "transaction_id": "TXN_NORMAL_SUCCESS",
        },
    )
    body = (await client.get("/api/metrics")).json()
    keys = {m["key"]: m for m in body["metrics"]}

    assert "autonomous_resolution_rate" in keys
    assert keys["human_hours_saved"]["simulated"] is True
    assert keys["autonomous_resolution_rate"]["simulated"] is False
    assert keys["audit_coverage"]["value"] is not None


async def test_voice_transcript_routes_through_the_text_pipeline(client):
    files = {"audio": ("clip.webm", b"not-real-audio", "audio/webm")}
    response = await client.post(
        "/api/voice/transcribe", files=files, data={"hint": "scenario_a"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["simulated"] is True
    assert "deducted" in body["text"]

    # The transcript is posted exactly like a typed message.
    created = await client.post(
        "/api/cases",
        json={"message": body["text"], "transaction_id": "TXN18293", "channel": "VOICE"},
    )
    assert created.status_code == 202
    assert created.json()["origin"] == "MERCHANT_VOICE"
    assert created.json()["intent"] == "PAYMENT_DEBITED_BUT_NOT_CONFIRMED"


async def test_settlement_completion_resolves_a_parked_case(client):
    created = await client.post(
        "/api/cases",
        json={
            "message": "My customer's payment failed, but the money was deducted.",
            "transaction_id": "TXN18293",
        },
    )
    case_id = created.json()["id"]
    assert created.json()["status"] == "VERIFYING"

    response = await client.post("/api/simulation/settlement/TXN18293/complete")
    assert response.status_code == 200
    assert case_id in response.json()["resumed_cases"]

    case = (await client.get(f"/api/cases/{case_id}")).json()
    assert case["status"] == "RESOLVED"


async def test_health_reports_live_providers(client):
    body = (await client.get("/api/health")).json()
    assert body["llm"]["provider"] == "mock"
    assert body["llm"]["simulated"] is True
    assert body["memory"]["provider"] == "local_index"


def test_simulation_state_is_reset_between_tests():
    assert simulation_state.fail_next_refund_attempts == 0
