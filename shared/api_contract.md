# Saarthi API contract

Conventions: JSON throughout, ISO-8601 UTC timestamps, string identifiers with stable prefixes
(`CASE-`, `TXN`, `EVT-`, `ACT-`, `RF-`, `TKT-`, `ESC-`, `MSG-`, `WF-`, `MEM-`, `ALERT-`).
Errors return `{"detail": "..."}`, or `{"error": {"status", "code", "message", "retryable"}}` from a
simulated enterprise system. Interactive docs are at `/docs`.

## Cases

| Endpoint | Purpose |
|---|---|
| `POST /api/cases` | Open a case. Returns **202** immediately; the agent runs in the background. Body: `{merchant_id?, message, transaction_id?, channel?}` |
| `GET /api/cases?status=&limit=` | List cases, newest first |
| `GET /api/cases/{id}` | Case snapshot |
| `POST /api/cases/{id}/message` | Add a merchant message. On a resolved case this opens a linked follow-up case |
| `POST /api/cases/{id}/resume` | Re-enter the agent loop. Body: `{trigger}` |
| `GET /api/cases/{id}/timeline` | Audit trail, ordered by sequence |
| `GET /api/cases/{id}/context` | Current state plus advisory memory |
| `GET /api/cases/{id}/messages` | The merchant-visible conversation (drafts excluded) |
| `GET /api/cases/{id}/workflows` | Workflow runs for this case |

### Case snapshot

```json
{
  "id": "CASE-18293",
  "status": "VERIFYING",
  "origin": "MERCHANT_CHAT",
  "merchant_id": "M1001",
  "transaction_id": "TXN18293",
  "intent": "PAYMENT_DEBITED_BUT_NOT_CONFIRMED",
  "diagnosis": { "root_cause": "SETTLEMENT_DELAY", "confidence": 0.94, "risk": "LOW", "summary": "…" },
  "risk": "LOW",
  "policy": { "action": "schedule_refund", "decision": "ALLOW", "policy_ids": ["POL-TXN-STATE"], "reasons": ["…"] },
  "current_action": { "type": "SCHEDULED_REFUND_WAIT", "status": "WAITING", "attempt": 1 },
  "requires_human": false,
  "human_required_by_policy": false,
  "wait_reason": "SETTLEMENT_PENDING",
  "resolution": null,
  "pending_escalation_id": null
}
```

`status` is one of RECEIVED, IDENTIFYING, INVESTIGATING, DIAGNOSING, POLICY_CHECK, PLANNING, ACTING,
VERIFYING, RECOVERING, RESOLVED, ESCALATED. `resolution` is AUTONOMOUS, HUMAN_APPROVED,
HUMAN_REJECTED or HUMAN_TAKEOVER.

### Timeline event

```json
{
  "id": "EVT-1007",
  "case_id": "CASE-18293",
  "type": "POLICY_CHECKED",
  "status": "SUCCESS",
  "actor": "SAARTHI",
  "message": "schedule_refund: ALLOW — Customer debit is confirmed…",
  "result": { "decision": "ALLOW", "policy_ids": ["POL-TXN-STATE"] },
  "sequence": 14,
  "timestamp": "2026-09-18T12:10:00Z"
}
```

`type` and `status` are always machine-readable. The dashboard never parses `message`.

## Escalations

| Endpoint | Effect |
|---|---|
| `GET /api/escalations?status=` | List, newest first |
| `GET /api/escalations/{id}` | One escalation with its full context snapshot |
| `POST /api/escalations/{id}/approve` | Record the override, then re-check policy and execute. **202** |
| `POST /api/escalations/{id}/reject` | Notify the merchant and resolve. **202** |
| `POST /api/escalations/{id}/takeover` | Transfer ownership; the agent stands down. **202** |

A second decision on a settled escalation returns **409**.

## Metrics, health and merchants

| Endpoint | Purpose |
|---|---|
| `GET /api/metrics` | Every metric, each with `value`, `unit`, `numerator`, `denominator` and a `simulated` flag |
| `GET /api/health` | The live provider for each layer |
| `GET /api/merchants` | Merchant list with refund authority and language |
| `GET /api/merchants/{id}/profile` | Operational history and recurring patterns, counted from Postgres |

## Voice

`POST /api/voice/transcribe` — multipart with `audio` and an optional `hint`. Returns a transcript
only. The caller then posts that text to the ordinary message endpoint.

`GET /api/voice/messages/{message_id}/speech` — `audio/wav` for a message Saarthi has already sent,
with `X-Saarthi-Voice-{Provider,Model,Language,Latency-Ms,Simulated,Cache}` and an immutable
`Cache-Control`. There is deliberately **no** endpoint that synthesises arbitrary text: the route
takes an id and re-reads the row, so every byte of audio has an audited message behind it.

| Status | Meaning |
|---|---|
| `404` | Unknown, inbound, unsent, or not stamped by the claims guard — all "not speakable" |
| `422` | Longer than `TTS_MAX_CHARACTERS`; never truncated, because half a sentence can invert it |
| `502` | The speech provider failed |
| `503` | `TTS_PROVIDER=off` |

## Simulation (demo controls)

| Endpoint | Effect |
|---|---|
| `POST /api/simulation/reset` | Restore the deterministic fixtures, including counters |
| `GET /api/simulation/scenarios` | The four scenarios with their suggested messages |
| `POST /api/simulation/scenario/{A\|B\|C\|D}` | Reset and arm one scenario |
| `POST /api/simulation/failure/refund` | Arm N failing refund attempts |
| `POST /api/simulation/settlement/{txn}/complete` | Settle, then resume any waiting case |
| `POST /api/simulation/settlement/{txn}/fail` | Fail settlement, then resume |
| `POST /api/simulation/proactive` | Arm and run the settlement monitor |
| `GET /api/simulation/alerts` | Active proactive alerts |
| `GET /api/simulation/state` | Current simulation state |

## Internal (n8n only)

All require the `X-Saarthi-Internal-Token` header; anything else returns **401**.

```
GET  /api/internal/cases/{case_id}/memory-document
POST /api/internal/memory/ingest
POST /api/internal/workflows/{run_id}/settlement | /complete | /execute
GET  /api/internal/settlements/pending-delayed
POST /api/internal/settlements/{transaction_id}/evaluate
POST /api/internal/actions/{action_id}/recover
POST /api/internal/escalations/{id}/workflow | /notify | /remind
```
