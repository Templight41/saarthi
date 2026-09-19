"""Changing how much Saarthi may refund without asking.

The refund limit is the line between an autonomous action and an escalation,
so the tests here are less about the endpoint working and more about the three
properties that make an editable limit safe: it is bounded, it is audited, and
the agent cannot reach it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

pytestmark = pytest.mark.asyncio


async def _limit(client, merchant_id: str = "M1001") -> Decimal:
    body = (await client.get("/api/merchants")).json()
    row = next(m for m in body["merchants"] if m["id"] == merchant_id)
    return Decimal(row["autonomous_refund_limit"])


async def _set(client, limit: str, merchant_id: str = "M1001", **over):
    return await client.patch(
        f"/api/merchants/{merchant_id}/refund-limit",
        json={"limit": limit, "changed_by": "ops@urbanthreads.in", **over},
    )


# --------------------------------------------------------------------------
# It changes what Saarthi is allowed to do
# --------------------------------------------------------------------------
async def _open(client, scenario_id: str) -> str:
    """Arm a scenario, then raise the case by hand.

    Deliberately not `/run`: that resets the fixtures, which is what makes the
    demo repeatable and would also undo the limit change under test.
    """
    await client.post(f"/api/scenarios/{scenario_id}/reset")
    scenario = (await client.get(f"/api/scenarios/{scenario_id}")).json()
    return scenario


async def _raise_case(client, scenario: dict) -> dict:
    created = await client.post(
        "/api/cases",
        json={
            "merchant_id": scenario["merchant_id"],
            "message": scenario["message"],
            "transaction_id": scenario["transaction_id"],
        },
    )
    return created.json()


async def test_lowering_the_limit_turns_an_autonomous_refund_into_an_escalation(client):
    """The point of the feature, end to end.

    Scenario B's ₹2,500 refund is inside Urban Threads' ₹5,000 limit and runs
    autonomously. Drop the limit below it and the same refund must stop and
    ask — with no cache to invalidate, because the policy engine re-reads the
    merchant on every decision.
    """
    scenario = await _open(client, "refund_failure")
    baseline = await _raise_case(client, scenario)
    assert baseline["status"] == "RESOLVED"
    assert baseline["resolution"] == "AUTONOMOUS"

    scenario = await _open(client, "refund_failure")
    await _set(client, "1000.00", reason="New merchant, low volume")
    constrained = await _raise_case(client, scenario)

    assert constrained["status"] == "ESCALATED"
    assert constrained["human_required_by_policy"] is True

    context = (await client.get(f"/api/cases/{constrained['id']}/context")).json()
    assert context["refunds"] == [], "nothing may be refunded once it is over the limit"


async def test_raising_the_limit_removes_the_amount_objection(client):
    """Urban Threads is loyal and high volume: ₹15,000 is within authority now.

    It still escalates, because a quality dispute is a judgement call — but on
    that rule alone. The two reasons were always independent, and only one of
    them is about money.
    """
    scenario = await _open(client, "high_value_dispute")
    await _set(client, "20000.00", reason="Five years, steady volume")
    case = await _raise_case(client, scenario)

    assert case["status"] == "ESCALATED"
    events = (await client.get(f"/api/cases/{case['id']}/timeline")).json()["events"]
    policy = [e["message"] for e in events if e["type"] == "POLICY_CHECKED"]
    assert policy, "the policy engine should have said something"
    assert not any("exceeds the autonomous refund limit" in m for m in policy)
    assert any("subjective" in m.lower() or "judgement" in m.lower() for m in policy)


# --------------------------------------------------------------------------
# Bounded
# --------------------------------------------------------------------------
async def test_the_limit_cannot_be_raised_past_the_ceiling(client):
    body = (await client.get("/api/merchants")).json()
    ceiling = Decimal(body["max_autonomous_refund_limit"])

    response = await _set(client, str(ceiling + Decimal("1.00")))
    assert response.status_code == 422
    assert "ceiling" in response.json()["detail"]
    assert await _limit(client) != ceiling + Decimal("1.00")


async def test_a_negative_limit_is_refused(client):
    assert (await _set(client, "-1.00")).status_code == 422


async def test_an_unknown_merchant_is_refused(client):
    assert (await _set(client, "1000.00", merchant_id="M9999")).status_code == 404


async def test_a_change_needs_someone_to_own_it(client):
    response = await client.patch(
        "/api/merchants/M1001/refund-limit", json={"limit": "1000.00", "changed_by": ""}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Audited
# --------------------------------------------------------------------------
async def test_every_change_records_who_what_and_why(client):
    before = await _limit(client)

    body = (await _set(client, "2500.00", reason="Chargeback rate rose")).json()
    assert body["changed"] is True
    entry = body["change"]
    assert entry["from"] == str(before)
    assert entry["to"] == "2500.00"
    assert entry["changed_by"] == "ops@urbanthreads.in"
    assert entry["reason"] == "Chargeback rate rose"
    assert entry["at"]

    listed = (await client.get("/api/merchants")).json()["merchants"]
    row = next(m for m in listed if m["id"] == "M1001")
    assert row["autonomous_refund_limit"] == "2500.00"
    assert row["limit_changed_by"] == "ops@urbanthreads.in"


async def test_the_history_survives_a_second_change(client):
    """JSON columns are not mutation-tracked; appending in place would vanish."""
    await _set(client, "2000.00", reason="first")
    await _set(client, "3000.00", reason="second")

    row = next(
        m for m in (await client.get("/api/merchants")).json()["merchants"] if m["id"] == "M1001"
    )
    reasons = [entry["reason"] for entry in row["limit_history"]]
    assert reasons == ["first", "second"]


async def test_setting_the_same_limit_is_not_recorded_as_a_change(client):
    current = await _limit(client)
    body = (await _set(client, str(current))).json()
    assert body["changed"] is False
    assert body["merchant"]["limit_history"] == []


async def test_limits_are_per_merchant(client):
    await _set(client, "1000.00", merchant_id="M1001")
    assert await _limit(client, "M1001") == Decimal("1000.00")
    # Kaveri's authority is its own.
    assert await _limit(client, "M1002") == Decimal("2000.00")


# --------------------------------------------------------------------------
# Not the agent's to change
# --------------------------------------------------------------------------
def test_the_agent_cannot_raise_its_own_authority():
    """An agent that can widen its own limit does not have one."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "saarthi"
    offenders = [
        path.relative_to(root)
        for package in ("agent", "tools", "policy", "verification", "recovery", "escalation")
        for path in (root / package).rglob("*.py")
        if "autonomous_refund_limit =" in path.read_text()
        or "refund-limit" in path.read_text()
    ]
    assert offenders == []


def test_no_tool_writes_to_a_merchant():
    from saarthi.tools.catalog import registry

    writers = [
        name
        for name in registry.names()
        if "merchant" in name and registry.get(name).side_effecting
    ]
    assert writers == []
