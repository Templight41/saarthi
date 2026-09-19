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
gcloud auth application-default login   # once
make setup      # dependencies, .env
make db-up      # PostgreSQL 17 with pgvector
make n8n-up     # n8n on :5678, workflows imported and published
make backend    # http://localhost:8000/docs
make frontend   # http://localhost:5173
```

Set `GOOGLE_CLOUD_PROJECT` in `.env` to your project. Nothing else is required: Vertex covers the
model, the embeddings and speech with one set of credentials.

### Why n8n runs on Node 24

n8n's `isolated-vm` native module does not build on Node 25, and its Docker image is unreachable
from networks that inspect TLS on large CDN transfers. `make n8n-up` therefore installs it under
Homebrew's `node@24` and runs it directly. Nothing else in the project cares which Node is used.

---

## Everything runs on real services

There are no stand-ins in the running system. Each capability is backed by a real service, and the
app **refuses to start** if a configuration would quietly substitute a deterministic one, naming
exactly which. Finding that out mid-demo is worse than not starting.

| Layer | What actually runs |
|---|---|
| Language model | Gemini 3.5 Flash on **Vertex AI** |
| Memory | **pgvector** similarity search over `gemini-embedding-001` vectors |
| Workflows | **n8n**, calling back into the API |
| Speech to text | **`gemini-3.5-transcribe-preview`** on Vertex AI |
| Text to speech | **`bulbul:v3`** on Sarvam |
| Database | **PostgreSQL 17** with pgvector |

The dashboard header carries an **all live** badge that turns amber the moment any layer is not what
it claims, and flags n8n as offline rather than green if it is configured but unreachable.

`ALLOW_SIMULATED=true` re-enables the deterministic providers. The test suite is the one place that
uses them, opted into explicitly, because a suite that called a live model would be slow, flaky and
billable.

What stays simulated, deliberately: **the merchant's payment systems**. The ledger, settlements and
refund gateway are the demo's subject matter, not a stand-in for anything Saarthi does, and the
specification rules out real payment integrations. No real money moves.

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

## Memory

Three layers, and the distinction between them is the whole point.

**What is true now** is PostgreSQL, and only PostgreSQL. **What does this remind us of** is
pgvector over a corpus of resolved cases — advisory, and labelled as such everywhere it appears.
**What keeps happening** is `memory/patterns.py`, which answers the question neither of the others
can: is this the merchant's third settlement delay this month?

Patterns are **counted, not inferred**. Each detector returns the rows it found — a timestamp, a
transaction id, a case you can open — and one shared rule turns a list of occurrences into a
pattern with a severity and a threshold. No model is consulted, because a model asked to count
produces a plausible number rather than a true one, and a merchant told "this is your third delay"
deserves that to be a fact.

Six detectors: settlement delays, payment-failure bursts, refund failures, repeated escalations,
case-volume spikes, and notification mismatches (which finds nothing until Phase 4's Soundbox
events exist — the correct answer, rather than a gap to wire up later).

Two details worth knowing. A burst is measured by **density, not total**: five failures across a
month is a business, five in ten minutes is a broken terminal, so only the tightest window counts.
A volume spike is measured against **the merchant's own baseline**, because a busy merchant with
steady volume is not an anomaly and a quiet one doubling is.

Patterns are computed on demand rather than stored. A patterns table would be a second place where
a number about the ledger lives, and it would go stale the moment a settlement completed.

They stay **advisory**. A pattern reaches the diagnosing model as context and the dashboard as
history. It is never an input to the policy engine — how often this has happened before does not
change what Saarthi is allowed to do about it today — and a test walks `policy/` to keep that true.

`GET /api/merchants/{id}/profile` is the merchant's operational history, every number traceable to
rows.

---

## Voice

Saarthi listens and answers aloud, and both directions lean on Sarvam because this is Indian
merchant support. On the one independent benchmark available, Sarvam's word error rate on Indian
English was 34.3 against Whisper large-v3's 46.8, and on Hindi 39.0 against 71.7. It also has a
code-mix mode, which is how these merchants actually speak. `make setup-voice` adds faster-whisper
for offline use; without either, a scripted transcriber keeps the demo alive.

**In.** The transcribe endpoint returns **only a transcript**. The browser then posts that text to
the ordinary message endpoint, so there is exactly one agent pipeline and it cannot tell speech from
typing. A test asserts that the same sentence sent as `CHAT` and as `VOICE` produces an identical
decision path, with the case's origin the only difference.

**Out.** `GET /api/voice/messages/{id}/speech` speaks a message Saarthi has **already sent**. It
takes an id and re-reads the row — the same discipline the verifier follows — and that is why there
is no endpoint that synthesises arbitrary text. Such an endpoint would let audio exist with no audit
row behind it; as built, every byte the merchant hears corresponds to a persisted message the claims
guard already checked against the ledger. A draft, an inbound message, or anything the guard never
stamped is simply not speakable.

Nothing under `agent/`, `tools/`, `workflows/` or `verification/` imports the synthesizer, and only
the browser calls the route, so **a dead speech provider cannot fail a case**. That is structural
rather than conventional, and `tests/test_voice.py` walks those packages to keep it so.

Saarthi answers in the medium the merchant used: a spoken question gets a spoken reply, a typed one
does not, and every message has a speaker button regardless.

**Language.** Each merchant carries a language (`merchants.language`), and Saarthi both *writes* and
speaks in it — so the audio and the text on screen are always the same words, never a translation of
each other. The language is recorded on the message rather than read from the merchant at playback,
because the template fallback writes English whatever the merchant speaks; taking it from the
message is what keeps an English fallback from being read aloud in a Hindi voice. Urban Threads is
served in English, Kaveri Foods in Hindi.

`make check-voice` calls the live endpoint and prints what came back. It is worth running before a
demo: bulbul:v2 was deprecated under us, and the failure was a flat 400 until the smoke test named
it. Speakers are tied to the model, so v2's `anushka` is refused by v3.

Set `TTS_PROVIDER=off` to run without speech output; the app refuses to start on `sarvam` with no
key rather than quietly going silent.

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
