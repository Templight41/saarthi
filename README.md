# Saarthi

**An autonomous merchant operations teammate.** Saarthi takes a merchant's problem, works out what is
actually true, decides what it is allowed to do, does it, checks that it worked, recovers when it
doesn't, and hands over to a person when the decision is genuinely theirs to make.

The thing that makes it more than a chatbot: **a case is only resolved once the business state has
been independently verified**. A tool returning success is not evidence that anything happened.

```
PERCEIVE → REMEMBER → REASON → CONTROL → ACT → VERIFY → RECOVER → ESCALATE
```

---

## The 90-second demo

Start everything (see [Quick start](#quick-start)), open http://localhost:5173, and press **Demo** in
the top bar.

1. **Run Scenario A.** The merchant says their customer's payment failed but the money was deducted.
   Watch the stage strip advance. Saarthi finds three similar past cases, sees that the payment is
   pending rather than failed, schedules a standby refund in case settlement never lands, tells the
   merchant the real position, and **waits**. It has not resolved anything yet, because it does not
   know yet.
2. **Press "Complete TXN18293".** Settlement lands. Saarthi verifies it, stands the standby refund
   down, tells the merchant, and resolves. Press **"Fail TXN18293"** instead and it refunds the
   customer rather than resolving.
3. **Run Scenario B.** The refund gateway fails on the first attempt. Saarthi does not retry blindly:
   it looks the refund up by its idempotency key, proves nothing happened, retries safely with the
   same key, verifies the result, and resolves. Exactly one refund exists at the end.
4. **Run Scenario C.** A ₹15,000 product-quality dispute. Saarthi refuses to refund it, because the
   amount is over its authority *and* the judgement is subjective. It escalates with the transaction,
   the dispute, the policy position, six completed checks and a recommendation. Press **Approve** and
   it runs the refund through the same executor and verifier as any autonomous action, recording the
   override in the audit trail.
5. **Press "Detect the overdue settlement".** A case opens for a merchant who never complained.

---

## Quick start

Requires Docker, Python 3.12 and Node with pnpm.

```bash
make setup     # dependencies, .env
make db-up     # PostgreSQL 17 with pgvector
make backend   # http://localhost:8000/docs
make frontend  # http://localhost:5173
```

No API keys are needed. Everything runs on deterministic local providers, and the dashboard shows
which ones are live.

---

## What is real, and what is a fallback

Every external dependency has a real implementation, a local fallback, and an environment switch. The
**fallback is the default**, so the demo never depends on venue wifi or a running container. The top
bar shows which one is actually live, so the demo is never dishonest about it.

| Layer | Real | Fallback (default) | Switch |
|---|---|---|---|
| Language model | Gemini on Vertex AI or AI Studio, or Sarvam | rule-based provider | `LLM_PROVIDER` |
| Memory | pgvector + Gemini embeddings | keyword index over the same documents | `MEMORY_PROVIDER` |
| Workflows | n8n Wait and Schedule nodes | in-process asyncio loops | `WORKFLOW_ENGINE` |
| Speech to text | Sarvam, or faster-whisper | scripted transcripts | `VOICE_PROVIDER` |
| Database | PostgreSQL | SQLite (tests only) | `DATABASE_URL` |

A real provider that fails at runtime degrades to the fallback and writes an `LLM_FALLBACK` event, so
the degradation shows up on the timeline instead of silently changing behaviour.

### Using Gemini on Vertex AI

The same SDK reaches two different backends, and `GEMINI_BACKEND` picks which.

```bash
gcloud auth application-default login
```

```ini
LLM_PROVIDER=gemini
GEMINI_BACKEND=vertex
GOOGLE_CLOUD_PROJECT=your-project
GOOGLE_CLOUD_LOCATION=global        # Gemini 3.x is served globally; regional endpoints 404
```

Vertex authenticates with Application Default Credentials, so **no API key is
involved**: quota, billing, audit logging and data residency all follow the Google Cloud project.
For an AI Studio key instead, set `GEMINI_BACKEND=developer` and `GEMINI_API_KEY`.

Embeddings follow the same setting, so chat and memory never end up on different backends. The
health endpoint reports the live backend as `gemini/vertex` or `gemini/developer`, and a
misconfigured Vertex setup degrades to the deterministic provider rather than failing at startup.

---

## Architecture

```
Merchant ── chat or voice ──►  FastAPI
                                  │
                          Saarthi supervisor  ◄── explicit state machine
                                  │
      ┌──────────────┬────────────┼────────────┬──────────────┐
      ▼              ▼            ▼            ▼              ▼
 PostgreSQL      Memory      Policy engine  Controlled    Workflow engine
 what is        what has     what is        tools         what happens
 true now       happened     allowed        what we can   over time
                  before                    actually do
                                  │
                              Verification  ── did it actually work?
                                  │
                               Recovery     ── what if it didn't?
                                  │
                              Escalation    ── when is this a person's call?
```

Each box answers exactly one question, and that separation is visible in the code:

| Question | Where it is answered |
|---|---|
| What is true right now? | PostgreSQL, via `services/` |
| What happened before? | `memory/` — advisory only, never authoritative |
| What should we do? | `agent/diagnosis.py` and `agent/planner.py` |
| Are we allowed to? | `policy/engine.py` |
| How do we actually do it? | `tools/` and `tools/executor.py` |
| Did it work? | `verification/verifier.py` |
| What if it failed? | `recovery/manager.py` |
| When does a person decide? | `escalation/manager.py` |
| What happens over time? | `workflows/` and n8n |

### Three decisions worth explaining

**The planner is deterministic; the model diagnoses and writes prose.** The specification says the
model proposes and the policy engine disposes, and that still holds: the diagnosis selects which plan
applies, and every resulting action is gated by policy. But letting the model emit the action list
directly would need a validation layer that reconstructs the planner anyway, and it would put the
demo at the mercy of a network call. The plan table is small and genuinely adaptable: Scenario A alone
walks three of its rows depending on what settlement does. The model's own `suggested_actions` are
recorded in the audit trail and never executed.

**Verification is structurally mandatory.** The state machine has no edge from `ACTING` to `RESOLVED`.
A case physically cannot resolve without passing through verification, and it cannot escalate from
verification without attempting recovery first. These are not conventions that a future change might
erode; they raise an exception.

**Correct escalation is not scored as failure.** Scenario C escalating is the system working, so
cases where policy *mandates* human review are excluded from the autonomy-rate denominator and
counted in the escalation rate instead. Otherwise the metric would punish the behaviour we want.

---

## Demo scenarios

| Scenario | Transaction | What it demonstrates |
|---|---|---|
| A | `TXN18293` ₹3,200 | A pending payment is not a failed payment. Diagnose, schedule a standby refund, wait, then resolve or refund depending on what settlement does. |
| B | `TXN_REFUND_FAILURE` ₹2,500 | The gateway fails once. Check whether the refund happened anyway, retry safely, verify, resolve. One refund, two attempts. |
| C | `TXN_HIGH_VALUE_DISPUTE` ₹15,000 | Above authority and subjective. Refuse, escalate with full context, execute only on approval. |
| D | `TXN_NORMAL_SUCCESS` ₹1,200 | Nothing is wrong. Say so and take no action. |
| Proactive | `TXN19931` ₹4,800 | Open a case for an overdue settlement before the merchant notices. |

All fixtures are deterministic, and `make demo-reset` restores them exactly, down to the case IDs.

---

## Safety model

- **Least privilege.** The agent gets a fixed tool registry. No SQL, no arbitrary HTTP.
- **Policy before action.** The executor refuses any side-effecting tool without an explicit ALLOW, so
  the policy engine cannot be bypassed by a new code path.
- **Verification before resolution.** Verifier functions take identifiers and re-read state; none of
  them accept an action result.
- **Idempotency.** Refund keys are derived from the case, transaction and amount, so a retry reuses
  the same key by construction. A unique constraint is the final backstop.
- **State check before retry.** Recovery asks whether the side effect already landed. If it cannot
  tell, it escalates rather than guessing.
- **Human control.** High-value, subjective and sensitive actions require a person. Approval is
  recorded as `APPROVED_BY_HUMAN` in the audit trail, and a non-overridable rule such as an invalid
  transaction state still blocks the action.
- **No false claims.** A message claiming a refund completed is rejected unless the refund ledger
  confirms it, and redrafted from a template. This is enforced in code, not in a prompt.
- **Fact clamping.** Deterministic rules override the model wherever the database disagrees, and every
  correction is recorded and shown in the UI.

---

## Voice

Sarvam is preferred for speech, because this is Indian merchant support. On the one independent
benchmark available, its word error rate on Indian English was 34.3 against Whisper large-v3's 46.8,
and on Hindi 39.0 against 71.7. It also has a code-mix mode, which is how these merchants actually
speak. `make setup-voice` adds faster-whisper for offline use; without either, a scripted transcriber
keeps the demo alive.

The transcribe endpoint returns **only a transcript**. The browser then posts that text to the
ordinary message endpoint, so there is exactly one agent pipeline and it cannot tell speech from
typing.

---

## n8n

n8n schedules and waits; the backend owns every decision and every side effect. Each n8n node that
does something is an HTTP call to a thin internal endpoint that invokes the same step function the
local engine calls directly, so switching engines cannot change behaviour.

```bash
make n8n-up       # http://localhost:5678
make n8n-import   # imports n8n/workflows/*.json
```

Then set `WORKFLOW_ENGINE=n8n`. If a webhook dispatch fails, that individual run continues locally and
says so on the timeline, rather than the case stalling.

---

## Testing

```bash
make test
```

54 backend tests and 12 dashboard tests. The ones that matter are the end-to-end scenario tests, which
assert on business state and the audit trail rather than on prose: Scenario A parks and then resolves
correctly on both branches, Scenario B leaves exactly one refund with two attempt rows in the right
order, and Scenario C refuses to refund, then resolves under an approval whose override is visible in
the timeline. One contract test asserts that every event ever emitted has a known type, a status and a
message, which is what lets the dashboard render the timeline without parsing prose.

---

## Limitations

- The enterprise systems are simulated. No real money moves.
- Demo timings are compressed. Settlement waits are seconds, not hours.
- There is no authentication or multi-tenancy. It is a single-merchant demo.
- Human hours saved is a configured estimate, flagged `simulated` in the UI. It is not a measurement,
  and it should not be presented as one.
- Sarvam showed the highest insertion rate of any system in that benchmark, which is a hallucination
  signature on long audio, so clips are capped.
- Metrics are computed over simulated data and labelled as such.
