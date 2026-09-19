# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Saarthi is an autonomous merchant-operations agent for an Indian payments platform. It takes a
merchant's problem, works out what is true, decides what it is allowed to do, does it, verifies the
result, recovers from failure, and hands over to a human when the decision is genuinely theirs.

The organising principle: **a case is RESOLVED only once the business state has been independently
verified.** A tool returning success is not evidence that anything happened. Most of the design below
exists to make that structurally true rather than merely conventional.

## Commands

```bash
make setup           # uv sync + pnpm install + .env
make db-up           # PostgreSQL 17 with pgvector (Docker)
make n8n-up          # import, publish and start n8n on :5678
make backend         # API on :8000  (docs at /docs)
make frontend        # dashboard on :5173
make test            # backend + frontend
make check-providers # prints which providers are actually live
```

Single test:
```bash
uv --project backend run pytest tests/test_policy_engine.py::test_invalid_transaction_state_is_denied -q
cd frontend && pnpm test -- timeline
```

Lint (ruff, line length 110): `uv --project backend run ruff check saarthi tests`

Demo controls, which are also how you drive things by hand:
```bash
curl -XPOST localhost:8000/api/simulation/reset              # restore fixtures, incl. counters
curl -XPOST localhost:8000/api/simulation/scenario/B         # reset + arm one scenario
curl -XPOST localhost:8000/api/simulation/settlement/TXN18293/complete
```

## No simulated providers

`ALLOW_SIMULATED=false` is the default and the app **refuses to start** if any configuration would
substitute a deterministic stand-in, listing which (`Settings.validate_providers`). Real providers do
not get wrapped in `FallbackProvider` unless simulation is allowed, so a runtime failure surfaces
rather than silently changing behaviour.

The test suite is the one legitimate user of the stand-ins and opts in explicitly in
`tests/conftest.py`. Do not add live-service calls to tests.

What stays simulated on purpose: **the merchant's payment systems** (`services/`). The ledger,
settlements and refund gateway are the demo's subject matter, not a stand-in for anything Saarthi
does. No real money moves.

## Architecture

Each module answers exactly one question, and that separation is the point:

| Question | Where |
|---|---|
| What is true right now? | `services/` over PostgreSQL |
| What happened before? | `memory/` — advisory only, never authoritative |
| What should we do? | `agent/diagnosis.py`, `agent/planner.py` |
| Are we allowed to? | `policy/engine.py` |
| How do we do it? | `tools/` and `tools/executor.py` |
| Did it work? | `verification/verifier.py` |
| What if it failed? | `recovery/manager.py` |
| When does a human decide? | `escalation/manager.py` |
| What happens over time? | `workflows/` and n8n |

`runtime.py` is the composition root; everything hangs off `SaarthiRuntime` so a test can swap one
collaborator.

### Invariants that are structural, not conventional

- **`ACTING → RESOLVED` is not a legal transition.** A case physically cannot resolve without passing
  through verification. `VERIFYING → ESCALATED` is likewise illegal, so a failure must attempt
  recovery first and that attempt is always auditable. Both raise `IllegalTransition`.
- **The executor refuses any side-effecting tool without an explicit policy ALLOW**
  (`PolicyViolation`). There is no code path that bypasses the policy engine.
- **Verifier functions take ids and re-read state.** None of them accept an action result.
- **Recovery never retries blind.** It asks whether the side effect already landed (refund lookup by
  idempotency key) and escalates when it cannot tell.
- **Idempotency keys are derived by the executor** from case, transaction and amount, so a retry
  reuses the same key by construction. A unique constraint on `refunds.idempotency_key` is the
  backstop.
- **The claims guard** (`agent/messaging.py`) rejects any merchant message claiming a refund or
  settlement completed unless the ledger confirms it. This is enforced in code, not in a prompt.
- **Fact clamping** (`agent/diagnosis.py`) overrides the model wherever the database disagrees, and
  records every correction in `clamped_fields`. Live models do get this wrong: Gemini 2.5 Flash
  misdiagnosed Scenario A as a confirmed payment failure.

### The supervisor loop

`agent/supervisor.py` runs one handler per state. Each iteration opens its **own session, runs one
handler, and commits**, so the dashboard can poll and watch the agent think. A handler returning
`next_state=None` **parks** the case — that is how Scenario A waits for settlement without holding a
task. Parking is not a transition.

`agent/runner.py` runs this in a tracked `asyncio` task, deliberately not FastAPI `BackgroundTasks`
(those die with the request's dependency scope and cannot be awaited from a test). Tests use
`agent_run_mode="inline"`.

### Planner is deterministic; the model diagnoses and writes prose

`agent/planner.py` maps `(intent, root cause, live facts)` to a plan. The model's diagnosis selects
the row and policy gates every step. This is not one hardcoded workflow: Scenario A walks three rows
depending on what settlement does. `Diagnosis.suggested_actions` from the model is validated against
the tool registry, logged, and **never executed**.

### Memory

Advisory context only. It runs its **own session** (`PgVectorMemory.session_factory`) — an earlier
version shared the caller's, and a failed vector query aborted the agent's transaction, after which
rolling back expired its ORM objects and the next statement died outside the greenlet context.
Merchant history always comes from PostgreSQL, never from memory search.

### Workflows

n8n schedules and waits; the backend owns every decision and side effect. Every n8n node that does
something calls a thin endpoint in `api/internal.py` that invokes the **same** step function in
`workflows/steps.py` that the in-process engine calls. The two engines cannot drift.

## Gotchas that have already cost time

**n8n**
- Expressions inside `jsonBody` evaluate only when the **whole parameter** starts with `=`. Inner
  `={{ }}` markers are literal text — URLs interpolate fine while request bodies arrive empty.
- Callbacks must use `127.0.0.1`, not `localhost`: Node resolves IPv6 first and uvicorn binds IPv4.
- Workflow JSON needs a stable top-level `id`, or re-importing creates duplicates and leaves the old
  broken copies active.
- CLI-imported credentials cannot be decrypted. The internal token is a plain header from `$env`.
- n8n runs under Homebrew `node@24` (its `isolated-vm` will not build on 25) from a project-local
  data folder, launched by `n8n/run-n8n.sh`. Its Docker image is unreachable on TLS-inspecting
  networks.

**Database**
- JSON columns are **not** mutation-tracked. Reassign a new dict; mutating in place is silently lost.
- `metadata` is reserved on `DeclarativeBase`, so audit metadata is the attribute `meta`.
- Enums are non-native with name == value, and `TZDateTime` forces UTC because SQLite drops tzinfo.
- IDs come from the `counters` table and reset with the seed, so the first case is always
  `CASE-18293`. Tests and the demo depend on that.

**Vertex AI**
- Gemini 3.x is served from the **`global`** endpoint; regional endpoints 404 even though the models
  appear in the catalogue.
- Use `pgvector.sqlalchemy.VECTOR` and do **not** call `register_vector` (raw-asyncpg only).
- Cast parameters compared only against NULL, or Postgres cannot infer their type.
- `gemini-embedding-001` below 3072 dims does not normalise; we L2-normalise ourselves.
- Embed in batches. Sequential per-document embedding blocked startup for minutes.

## Metrics

Cases escalated **because policy mandates a human** carry `human_required_by_policy` and are excluded
from the autonomy-rate denominator. Scenario C escalating is the system working; counting it as a
failure would punish the behaviour we want. `human_hours_saved` is a configured estimate and is
flagged `simulated` in the API and the UI.

## Frontend

Polling via TanStack Query, not WebSockets. The timeline renders from `type` and `status` on each
event and never parses prose — keep new events machine-readable.

Colour carries state only: amber acting, teal verified, red needs-human, violet memory. Tokens live
in `src/styles/theme.css`. The base colour token is named `ground`, not `base`, because `--color-base`
shadows Tailwind's `text-base` font-size utility.
