# What a real payment-platform integration would require

**None of this is implemented.** Saarthi runs against a simulated payment platform, and this
document exists so that the gap between the demo and a production deployment is written down
rather than glossed over. Everything below is a requirement, not a feature.

The one thing that *is* implemented is the shape of the seam: `services/providers.py` defines the
protocols a real adapter would have to satisfy, and `tests/test_payment_domain.py` asserts the
current PostgreSQL-backed modules still satisfy them. Swapping in a real platform means writing new
implementations of those protocols. It does not mean rewriting the agent, the policy engine or the
verifier, and that is the point of the boundary.

```
Merchant (chat / voice)
        ↓
   API gateway                    ← auth, rate limits, tenant routing
        ↓
 Saarthi orchestrator             ← the supervisor loop, unchanged
        ↓
 policy · memory · diagnosis
        ↓
   tool registry                  ← the only way to reach anything external
        ↓
   domain adapters                ← services/providers.py
        ↓
 payment platform APIs            ← today: PostgreSQL fixtures
        ↓
   verification                   ← re-reads through the same adapters
        ↓
  audit / observability
```

---

## Identity and access

| Requirement | Why it bites here specifically |
|---|---|
| Service authentication | The API has **no authentication at all** today. Anyone who can reach it can open a case and drive the agent. |
| Per-merchant authorisation | Nothing currently stops a request naming another merchant's transaction id. `notification_service.reconcile` scopes lookups to the announcing merchant; nothing else does. |
| Tenant isolation | Every query would need a tenant predicate, enforced at the adapter rather than trusted from the caller. |
| Scoped platform credentials | A refund credential is not a read credential. The agent reads constantly and writes rarely, and the blast radius of the two should not be the same. |
| Secrets management | Keys currently come from a `.env` file. Production needs a secret manager, rotation, and no key ever reaching a log or an error body. |

## Correctness under load

- **Idempotency across processes.** Keys are derived deterministically by the executor, and a
  unique constraint on `refunds.idempotency_key` is the backstop. Against a real platform the
  platform's own idempotency window matters too, and it is usually shorter than a case's lifetime.
- **Rate limiting and backpressure.** The verifier re-reads on every check. At scale that is a lot
  of reads, and a settlement monitor scanning every merchant is a thundering herd waiting to happen.
- **API versioning.** A payment platform will change response shapes. The adapter is the only place
  that should know which version it is talking to.
- **Retries and timeouts.** Currently a single timeout per provider. Production needs per-operation
  budgets, jitter, and a circuit breaker — with the existing rule preserved: *never retry a
  side-effecting call without first asking whether it landed.*
- **Eventual consistency.** A real ledger may not reflect a write immediately. Verification would
  need a bounded read-after-write wait, and `VERIFYING → RESOLVED` must stay impossible until it
  genuinely confirms.

## Data and privacy

- Merchant and customer data are personal data under the DPDP Act. Retention, deletion and purpose
  limitation all apply, including to the **memory layer** — an embedding of a case is still a record
  of that case.
- Message bodies and transcripts go to third-party model providers. That needs a lawful basis, a
  processor agreement, and a way to run without it.
- Audio is biometric-adjacent. Today transcription audio is written to a temp directory and deleted
  after the request; that is the right default and should stay explicit.
- PII redaction before anything reaches a model or a log.

## Human control

- The approval surface is currently the dashboard with no identity behind it: `decided_by` is a
  string the client supplies. Production needs authenticated approvers, and an approval that is
  bound to the specific action it approved.
- Four-eyes review above a threshold, and an emergency stop that halts the agent across all cases.
- An auditable record of *who* approved *what*, retained independently of the case.

## Operations

- Distributed tracing spanning case → tool call → platform request, so a slow refund can be
  attributed.
- Metrics that distinguish "the agent decided not to act" from "the agent could not act", because
  the autonomy rate is meaningless if those are the same number.
- Alerting on escalation rate, verification-failure rate and recovery exhaustion, not just errors.
- A sandbox environment with the platform's own test credentials, and a gradual rollout: read-only
  first, then low-value refunds, then the rest.
- Replay of a case against a new model or policy version before it ships.

---

## What the simulation deliberately keeps

The merchant's payment systems under `services/` stay simulated on purpose. They are the demo's
subject matter, not a stand-in for something Saarthi does, and no real money moves. Everything
Saarthi itself depends on — the model, embeddings, speech, workflow engine — runs against real
providers, and the application refuses to start if a deterministic stand-in would be substituted
for any of them.
