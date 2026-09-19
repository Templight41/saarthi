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
| What was the merchant *told*? | `services/notification_service.py` — evidence, never fact |
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
- **Speech output takes a message id and re-reads the row.** There is deliberately no endpoint that
  synthesises arbitrary text, because that would let audio exist with no audit row behind it.
  A draft, an inbound message, or anything without a claims-guard stamp in `message.meta["claims"]`
  is not speakable. `ops_service.is_merchant_visible` is the single predicate, shared with the
  messages endpoint.
- **Nothing outside `api/voice.py` imports the synthesizer**, so a dead speech provider cannot fail
  a case. `tests/test_voice.py::test_the_agent_never_reaches_for_the_synthesizer` walks `agent/`,
  `tools/`, `workflows/`, `verification/`, `recovery/`, `escalation/` and `policy/` to keep it true.
- **The spoken language comes from the message, not the merchant.** The template fallback writes
  English whoever it stands in for, so reading the language off the merchant would have bulbul read
  English words in a Hindi voice.
- **A notification is evidence; the ledger is the fact.** A Soundbox announcement lives in
  `notification_events`, whose `reference` is plain text rather than a foreign key — so a device
  can announce a payment the ledger has never heard of, which is exactly the case Saarthi must not
  resolve by inventing a transaction. `notification_service.reconcile` is the only crossing, it
  re-reads authoritative state, and it never writes.
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

Three layers. Postgres is what is true now; pgvector is what this reminds us of; `memory/patterns.py`
is what keeps happening. Patterns are **counted from rows, never inferred by a model** — each
detector returns the occurrences it found and one shared rule in `_build` turns a count into a
severity, so "would this have fired?" is answerable by reading `RULES`.

Patterns are computed on demand, not stored: a patterns table would be a second place where a
number about the ledger lives, and it would go stale the moment a settlement completed.

They are advisory like everything else historical. `policy/` must never import them, and
`test_patterns.py::test_the_policy_engine_cannot_see_patterns` walks the package to enforce it.

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
  `CASE-18293`. Tests and the demo depend on that. It also means an id alone does not identify a
  message, which is why the speech cache is keyed on a hash of the content too.
- There is no Alembic: `init_db` only runs `create_all`, so a new column needs `make db-reset`.
- `Settings` reads `../.env`, so `tests/conftest.py` passes `_env_file=None`. Without it a developer
  machine with a real key builds real providers in the test suite — `allow_simulated` *permits*
  stand-ins, it does not force them.

**Voice**
- Sarvam TTS returns `audios` as a list of base64 **chunks**. Taking `[0]` truncates what Saarthi
  says mid-sentence. `decode_audios` decodes first and splices whole WAVs when each chunk carries
  its own RIFF header, and falls back to Sarvam's documented join-then-decode otherwise.
- **bulbul:v2 is deprecated** and returns a flat 400 from the live API. Speakers are tied to the
  model: v2's `anushka` is refused by v3, whose roster is `shubh`, `aditya`, `ritu`, … `make
  check-voice` calls the real endpoint and is how this was found.
- The API **ignores unknown body fields** rather than rejecting them, so a 200 is no evidence a
  parameter was honoured — and output is non-deterministic even at `temperature: 0.01`, so you
  cannot A/B it by comparing bytes either. Follow the documentation: v3 wants `language_code`
  (`target_language_code` is the legacy name and is probably just being ignored).
- A 4xx body carries Sarvam's own `error.message`, which names a deprecated model or an
  incompatible speaker exactly. `_explain` passes that through for client errors only; a 5xx gets
  nothing but its status.
- Browsers reject a delayed `audio.play()` with `NotAllowedError` when there has been no recent user
  gesture, and StrictMode's double-invoked effect produces `AbortError`. Both are swallowed; a
  message id is added to the spoken set *before* play precisely to avoid the second.
- `expose_headers` on the CORS middleware is what makes `X-Saarthi-Voice-*` readable in the browser.
  `allow_headers` governs the request, not the response, so this breaks silently the moment the
  dashboard stops proxying through Vite.

**Vertex AI**
- Gemini 3.x is served from the **`global`** endpoint; regional endpoints 404 even though the models
  appear in the catalogue.
- Use `pgvector.sqlalchemy.VECTOR` and do **not** call `register_vector` (raw-asyncpg only).
- Cast parameters compared only against NULL, or Postgres cannot infer their type.
- `gemini-embedding-001` below 3072 dims does not normalise; we L2-normalise ourselves.
- Embed in batches. Sequential per-document embedding blocked startup for minutes.

## The Scenario Lab

`simulation/scenarios.py` holds exactly five scenarios, and they are **data**: the agent has no
idea which one is running, and `tests/test_scenario_lab.py` walks `agent/`, `policy/`,
`verification/`, `recovery/` and `escalation/` to keep it that way. Arming a scenario shapes the
world — which fixtures exist, whether the refund gateway fails — never the reasoning.

A scenario's checkpoints are satisfied by an **audit event or a case status**, never by parsing
prose, so `GET /api/scenarios/{id}/status` is an observation rather than a claim. A scenario that
quietly broke reports unticked checkpoints.

Two of the five end with Saarthi stopping. That is the behaviour being demonstrated, and both carry
`human_required_by_policy`, so they are excluded from the autonomy-rate denominator.

`resolve()` accepts `A`/`B`/`C` for the three original scenarios. The two newer ones are
addressable only by id, deliberately: a presenter typing `D` gets a 404 rather than a different
scenario than the one they meant.

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
