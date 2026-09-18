# SAARTHI — MASTER IMPLEMENTATION PROMPT

You are the primary autonomous software engineer responsible for building the complete Saarthi hackathon project from the specification below.

You have ownership of the entire repository: backend, frontend, database, AI/agent orchestration, simulated enterprise APIs, Cognee memory, n8n workflows, voice interface, verification, recovery, escalation, audit logging, analytics, testing, Docker setup, documentation, and demo scenarios.

Do NOT merely generate a scaffold.

Your objective is to build a **fully runnable end-to-end hackathon prototype** that demonstrates genuine autonomous merchant operations.

---

# 1. PRODUCT

## Name

**Saarthi**

## Positioning

**Saarthi — Autonomous Merchant Operations Teammate**

Saarthi is not a generic FAQ chatbot.

Saarthi is an AI teammate that owns a merchant's operational problem from beginning to end:

> Perceive → Remember → Reason → Control → Act → Verify → Recover → Escalate

The system should:

1. Understand a merchant request from text or voice.
2. Identify the relevant merchant, transaction, settlement, dispute, or case.
3. Retrieve current business state.
4. Retrieve relevant historical/contextual memory.
5. Diagnose the issue.
6. Determine what needs to be true for the issue to actually be resolved.
7. Formulate a goal-oriented plan.
8. Check policy and authorization before taking side-effecting actions.
9. Execute permitted actions through controlled tools/APIs.
10. Verify the resulting business state.
11. Recover safely when actions fail.
12. Escalate when human judgment, authorization, verification, or intervention is genuinely required.
13. Record every significant event in an audit trail.
14. Continue long-running workflows through n8n where appropriate.
15. Store useful historical case knowledge in Cognee.
16. Surface everything clearly in a React merchant/support dashboard.

The system should optimize for **verified business outcomes**, not merely good conversational responses.

---

# 2. CORE DESIGN PRINCIPLE

The most important principle is:

> **Saarthi owns the outcome, not merely the conversation.**

Never consider a case resolved merely because:

- the LLM generated a response,
- a tool returned HTTP 200,
- a refund request was submitted,
- an n8n workflow started,
- or a message was sent.

A case becomes RESOLVED only when the intended business state has been independently verified.

For example:

BAD:

```text
issue_refund() → success → resolve case
```

GOOD:

```text
issue_refund()
      ↓
get_refund_status()
      ↓
refund = COMPLETED
      ↓
case = RESOLVED
```

---

# 3. REQUIRED TECHNOLOGY STACK

Use the following stack unless there is a compelling implementation reason not to.

## Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- PostgreSQL
- Async database operations where practical

## AI

- Google Gemini and/or Groq
- Google ADK where practical for agent orchestration
- Structured JSON outputs
- Tool/function calling

The architecture must not depend on a single model provider if it can reasonably support provider abstraction.

Create a model abstraction such as:

```text
LLMProvider
├── GeminiProvider
└── GroqProvider
```

Use environment variables to choose the provider.

## Memory / Knowledge

Cognee.

Use Cognee as the semantic/relationship memory layer, NOT as the transactional source of truth.

PostgreSQL remains authoritative for current transaction/case/payment/refund state.

Cognee should provide:

- merchant historical context
- previous cases
- previous resolutions
- operational knowledge
- policy knowledge retrieval
- relevant support knowledge
- semantic similarity
- relationship/context retrieval

## Workflow automation

n8n.

Use n8n for:

- scheduled workflows
- delayed settlement monitoring
- scheduled refund conditions
- long-running workflows
- event-driven automation
- human approval workflow integration
- failure/retry workflows where appropriate
- proactive merchant notifications

Do NOT move the entire agent brain into n8n.

Saarthi remains the reasoning/orchestration brain.

## Voice

- Whisper for speech-to-text
- VAD where practical

Voice input must eventually feed into the exact same text/agent pipeline.

Architecture:

```text
Voice → Whisper → Text → Saarthi pipeline
Chat  → Text ───────────→ Saarthi pipeline
```

Do not maintain separate business logic for voice.

## Frontend

- React
- Vite
- modern component architecture
- responsive dashboard
- clean professional merchant operations UI

## Optional

- Redis only if genuinely useful
- WebSockets if easy
- polling is acceptable for hackathon real-time updates

Do not introduce infrastructure merely for architectural appearance.

---

# 4. REPOSITORY STRUCTURE

Create a clean structure similar to:

```text
saarthi/
│
├── backend/
│   ├── api/
│   ├── agent/
│   │   ├── supervisor.py
│   │   ├── diagnosis.py
│   │   ├── planner.py
│   │   ├── state_machine.py
│   │   └── prompts.py
│   │
│   ├── tools/
│   │   ├── transaction.py
│   │   ├── settlement.py
│   │   ├── refund.py
│   │   ├── dispute.py
│   │   ├── ticket.py
│   │   ├── messaging.py
│   │   ├── merchant.py
│   │   └── policy.py
│   │
│   ├── policy/
│   │   └── engine.py
│   │
│   ├── verification/
│   │   └── verifier.py
│   │
│   ├── recovery/
│   │   └── manager.py
│   │
│   ├── escalation/
│   │   └── manager.py
│   │
│   ├── memory/
│   │   ├── cognee_client.py
│   │   └── memory_service.py
│   │
│   ├── simulation/
│   │   ├── scenarios/
│   │   └── failure_injection.py
│   │
│   ├── database/
│   │   ├── models.py
│   │   ├── database.py
│   │   └── seed.py
│   │
│   ├── services/
│   │
│   ├── schemas/
│   │
│   ├── tests/
│   │
│   └── main.py
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── services/
│   │   ├── hooks/
│   │   ├── types/
│   │   ├── mock/
│   │   └── App.*
│   │
│   └── package.json
│
├── n8n/
│   ├── workflows/
│   │   ├── case_memory_ingestion.json
│   │   ├── scheduled_refund.json
│   │   ├── settlement_monitor.json
│   │   ├── failure_recovery.json
│   │   └── human_approval.json
│   └── README.md
│
├── shared/
│   ├── api_contract.md
│   └── schemas/
│
├── tests/
│
├── docker-compose.yml
├── .env.example
├── README.md
└── Makefile or equivalent
```

Adapt the exact structure if necessary, but preserve clean separation between responsibilities.

---

# 5. DATABASE

Use PostgreSQL.

Create models/tables for at least:

```text
merchants
transactions
settlements
disputes
cases
refunds
tickets
policies
actions
agent_events
escalations
messages
```

## Merchant

Fields should include:

- id
- name
- email
- phone
- risk_level
- autonomous_refund_limit
- created_at

## Transaction

Include:

- id
- merchant_id
- amount
- currency
- payment_status
- customer_debited
- customer_reference
- created_at
- updated_at

## Settlement

Include:

- id
- transaction_id
- status
- expected_at
- completed_at
- delay_reason
- updated_at

## Dispute

Include:

- id
- transaction_id
- type
- description
- requested_amount
- status
- evidence
- created_at

## Case

Include:

- id
- merchant_id
- transaction_id nullable
- intent
- status
- diagnosis
- confidence
- risk
- requires_human
- created_at
- updated_at
- resolved_at

## Refund

Include:

- id
- transaction_id
- amount
- status
- reason
- attempt_count
- created_at
- updated_at

## Ticket

Include:

- id
- case_id
- status
- priority
- subject
- description
- created_at
- updated_at

## Policy

Include:

- id
- name
- policy_type
- configuration
- active

## Action

Include:

- id
- case_id
- action_type
- status
- input
- result
- attempt
- created_at
- completed_at

## Agent Event

Include:

- id
- case_id
- event_type
- actor
- message
- input
- result
- metadata
- timestamp

## Escalation

Include:

- id
- case_id
- reason
- recommendation
- status
- assigned_to
- human_decision
- created_at
- resolved_at

---

# 6. DETERMINISTIC DEMO DATA

Do not depend on random data for the primary demo.

Create deterministic scenarios.

## Scenario A — Settlement delay

Merchant:

```text
Urban Threads
```

Transaction:

```text
TXN18293
₹3,200
```

State:

```text
payment status = PAYMENT_PENDING
customer_debited = true
settlement = PENDING
delay_reason = BANK_CONFIRMATION
```

Merchant complaint:

```text
"My customer's payment failed, but the money was deducted."
```

Expected diagnosis:

```text
Likely settlement delay rather than confirmed payment failure.
```

Expected risk:

```text
LOW
```

Expected policy:

```text
refund/scheduled-refund eligible under configured conditions
```

---

## Scenario B — Refund API failure

Transaction:

```text
TXN_REFUND_FAILURE
₹2,500
```

Refund:

```text
eligible
```

Configure simulated refund API:

```text
first attempt → failure
second attempt → success
```

Expected behavior:

```text
refund requested
→ API failure
→ verify whether refund nevertheless happened
→ refund not found
→ safe retry
→ retry succeeds
→ verify refund
→ resolve
```

---

## Scenario C — High-value subjective dispute

Transaction:

```text
TXN_HIGH_VALUE_DISPUTE
₹15,000
```

Merchant request:

```text
"The customer says the product quality was poor and wants a ₹15,000 partial refund."
```

Expected behavior:

```text
product-quality dispute
+
subjective judgment
+
amount above autonomous authority
→ human approval required
```

Saarthi must NOT autonomously issue the refund.

---

## Scenario D — Normal successful transaction

Transaction:

```text
TXN_NORMAL_SUCCESS
₹1,200
payment = SUCCESS
settlement = COMPLETED
```

Use this to demonstrate that Saarthi doesn't perform unnecessary actions.

---

# 7. SIMULATED ENTERPRISE APIs

Build controlled internal APIs/services representing enterprise systems.

## Ledger / Transaction

```text
GET /transactions/{id}
GET /transactions/{id}/history
GET /transactions/{id}/payment-history
```

## Settlement

```text
GET /settlements/{transaction_id}
GET /settlements/{transaction_id}/eta
```

## Dispute

```text
GET /disputes/{transaction_id}
POST /disputes
PATCH /disputes/{id}
```

## Refund

```text
POST /refunds
POST /refunds/schedule
GET /refunds/{id}
```

## Ticket

```text
POST /tickets
PATCH /tickets/{id}
```

## Communication

```text
POST /messages/draft
POST /messages/send
```

## Merchant

```text
GET /merchants/{id}
GET /merchants/{id}/history
```

## Policy

```text
GET /policies/{policy_name}
POST /policies/check
```

These APIs must have controlled, deterministic behavior so the hackathon demo is reliable.

---

# 8. FAILURE INJECTION

Implement a clean mechanism to deliberately cause failures.

For example:

```text
FAIL_FIRST_REFUND=true
```

or a simulation endpoint.

Do not randomly fail production behavior.

Support:

```text
refund first attempt failure
```

as the main demo failure.

Expose a development/demo control if useful:

```text
POST /simulation/reset
POST /simulation/scenario/{scenario_name}
POST /simulation/failure/refund
```

The demo should be repeatable.

---

# 9. AGENT STATE MACHINE

Do not allow the LLM to freely invent workflow states.

Implement explicit states:

```text
RECEIVED
IDENTIFYING
INVESTIGATING
DIAGNOSING
POLICY_CHECK
PLANNING
ACTING
VERIFYING
RECOVERING
RESOLVED
ESCALATED
```

Typical flow:

```text
RECEIVED
↓
IDENTIFYING
↓
INVESTIGATING
↓
DIAGNOSING
↓
POLICY_CHECK
↓
PLANNING
↓
ACTING
↓
VERIFYING
↓
RESOLVED
```

Failure:

```text
VERIFYING
↓
RECOVERING
↓
ACTING
↓
VERIFYING
```

Exhausted recovery:

```text
RECOVERING
↓
ESCALATED
```

---

# 10. SUPERVISOR AGENT

Create a central Saarthi supervisor.

Its responsibilities:

- understand objective
- identify relevant entities
- retrieve current context
- retrieve memory
- diagnose
- determine risk
- formulate plan
- request policy checks
- choose permitted tools
- execute actions
- verify outcomes
- recover
- escalate
- update case
- communicate with merchant

The supervisor should never directly access the database for side effects.

It should use controlled tools/services.

---

# 11. STRUCTURED DIAGNOSIS

The model should output structured data similar to:

```json
{
  "intent": "PAYMENT_DEBITED_BUT_NOT_CONFIRMED",
  "transaction_id": "TXN18293",
  "root_cause": "SETTLEMENT_DELAY",
  "confidence": 0.94,
  "risk": "LOW",
  "requires_human": false
}
```

Possible intents:

```text
PAYMENT_FAILED
PAYMENT_DEBITED_BUT_NOT_CONFIRMED
SETTLEMENT_DELAY
REFUND_REQUEST
REFUND_STATUS
PRODUCT_QUALITY_DISPUTE
HIGH_VALUE_REFUND
GENERAL_TRANSACTION_QUERY
UNKNOWN
```

If confidence is below a configurable threshold, escalate rather than fabricate certainty.

---

# 12. CONTEXT ENGINE

For every case, build a context object containing:

```text
merchant
transaction
payment history
settlement
dispute
previous cases
previous actions
relevant policy
Cognee memory
```

The agent should reason over structured current state plus semantic historical context.

---

# 13. COGNEE MEMORY

Integrate Cognee meaningfully.

Do NOT use Cognee merely as a checkbox.

Cognee should provide persistent semantic/relationship memory.

Use it for:

## Merchant memory

Store:

- historical support cases
- recurring operational issues
- previous resolutions
- useful merchant preferences
- relevant merchant operational context

## Case memory

After resolution, store:

- merchant complaint
- transaction context
- diagnosis
- actions
- failures
- recovery
- outcome

## Policy knowledge

Store policy documentation / structured policy explanations as semantic knowledge.

## Support knowledge

Store:

- settlement procedures
- refund procedures
- common failure modes
- escalation guidelines
- communication guidance

---

# 14. COGNEE MEMORY FLOW

During a case:

```text
new case
↓
Cognee search
↓
retrieve relevant previous cases / knowledge
↓
inject into Saarthi context
```

After case resolution:

```text
case resolved
↓
n8n workflow
↓
Cognee add
↓
cognify
```

Do not block the primary user workflow unnecessarily on memory ingestion.

Cognee should complement PostgreSQL.

PostgreSQL = current transactional truth.

Cognee = semantic memory.

---

# 15. COGNEE SERVICE

Create an abstraction:

```python
class SaarthiMemory:

    async def search(query):
        ...

    async def remember_case(case):
        ...

    async def remember_conversation(conversation):
        ...

    async def remember_action(action):
        ...

    async def add_knowledge(content):
        ...
```

Keep Cognee-specific implementation isolated.

Use environment variables for:

```text
COGNEE_API_URL
COGNEE_API_KEY
```

If credentials are unavailable during local development, implement a graceful mock/fallback memory provider so the entire application still runs.

Do not hardcode credentials.

---

# 16. POLICY ENGINE

This is critical.

The LLM proposes actions.

The policy engine determines whether those actions are permitted.

Never allow the LLM to bypass the policy engine.

Architecture:

```text
LLM
↓
proposed action
↓
Policy Engine
↓
ALLOW / DENY / REQUIRES_APPROVAL
```

Example:

```text
Refund ₹3,200
merchant limit = ₹5,000
eligible = true
risk = low
→ ALLOW
```

Example:

```text
Refund ₹15,000
merchant limit = ₹5,000
→ REQUIRES_APPROVAL
```

Example:

```text
product quality dispute
→ subjective judgment
→ REQUIRES_APPROVAL
```

Example:

```text
invalid transaction state
→ DENY
```

Policy decisions must be stored in the audit trail.

---

# 17. POLICY EXAMPLES

Implement policies such as:

### Refund limit

```text
≤ ₹5,000 → potentially autonomous
> ₹5,000 → human approval
```

### Product-quality disputes

```text
subjective dispute → human review
```

### Sensitive account changes

```text
human/additional verification required
```

### Pending payment

```text
investigate settlement before declaring failure
```

### Retry

Only retry safe/idempotent operations after checking current state.

---

# 18. ACTION EXECUTOR

Create controlled tools/functions:

```text
get_merchant()
get_transaction()
get_payment_history()
get_settlement_status()
get_settlement_eta()
get_dispute()

check_policy()
check_refund_eligibility()

issue_refund()
schedule_refund()

create_ticket()
update_ticket()

draft_message()
send_message()

verify_refund()
verify_settlement()

create_escalation()
```

The LLM must never receive arbitrary SQL or unrestricted HTTP access.

---

# 19. GOAL-BASED EXECUTION

Saarthi should receive a high-level goal such as:

```text
Resolve the merchant's complaint about transaction TXN18293.
```

The system should determine the required steps.

Example:

```text
1. Identify transaction
2. Retrieve transaction state
3. Retrieve settlement
4. Diagnose
5. Retrieve relevant policy
6. Check authorization
7. Select action
8. Execute
9. Verify
10. Recover if needed
11. Notify merchant
12. Resolve or escalate
```

Do not hardcode one giant workflow that only works for one sentence.

Use tools, state, structured decisions, and policy constraints to make the workflow adaptable.

---

# 20. VERIFICATION ENGINE

Create explicit verification functions.

Examples:

```text
verify_transaction_state()
verify_settlement_state()
verify_refund_state()
verify_ticket_state()
verify_message_status()
```

Verification must query current state independently of the action request.

Example:

```text
issue_refund()
↓
get_refund()
↓
status == COMPLETED
```

Only then:

```text
case.status = RESOLVED
```

---

# 21. RECOVERY ENGINE

Implement:

```text
detect failure
↓
classify failure
↓
check current backend state
↓
determine whether side effect already occurred
↓
if safe:
    retry
↓
verify
↓
if unsuccessful:
    try permitted alternative
↓
if exhausted:
    escalate
```

Never blindly retry side effects.

Use idempotency/state checks.

Main demo:

```text
Refund API
↓
500
↓
verify refund state
↓
not processed
↓
retry
↓
success
↓
verify
↓
resolved
```

---

# 22. ESCALATION ENGINE

Escalate when:

- human judgment is required
- requested amount exceeds authority
- policy exception is requested
- confidence is too low
- risk is too high
- repeated technical failures remain unresolved
- merchant explicitly asks for a human
- sensitive action requires human intervention

Escalation object should include:

```json
{
  "case_id": "CASE-18293",
  "reason": "HIGH_VALUE_SUBJECTIVE_DISPUTE",
  "risk": "HIGH",
  "amount": 15000,
  "completed_actions": [
    "transaction_verified",
    "dispute_history_checked",
    "policy_checked"
  ],
  "recommendation": "REVIEW_PARTIAL_REFUND",
  "status": "PENDING_HUMAN"
}
```

Never escalate with only:

```text
"AI could not resolve this."
```

Provide complete context.

---

# 23. HUMAN APPROVAL WORKSPACE

Build UI/API support for:

```text
Case #18293

Issue:
High-value merchant refund

Transaction:
₹15,000

Context:
Customer disputes product quality

Policy:
Requested action exceeds autonomous authority

Actions already completed:
✓ transaction verified
✓ dispute history checked
✓ policy retrieved

AI recommendation:
Review partial refund

Human actions:
APPROVE
REJECT
TAKE OVER
```

If APPROVE:

```text
human approval
↓
controlled action
↓
verification
↓
resolution
```

If REJECT:

```text
case updated
↓
merchant notified
```

If TAKE OVER:

```text
AI stops autonomous execution
↓
case ownership transferred
```

---

# 24. AUDIT TRAIL

Record every significant event.

Examples:

```text
CASE_CREATED
MERCHANT_IDENTIFIED
TRANSACTION_RETRIEVED
SETTLEMENT_CHECKED
MEMORY_RETRIEVED
DIAGNOSIS_COMPLETE
POLICY_CHECKED
ACTION_STARTED
ACTION_FAILED
RECOVERY_STARTED
ACTION_RETRIED
ACTION_VERIFIED
MESSAGE_DRAFTED
MESSAGE_SENT
ESCALATION_CREATED
HUMAN_APPROVED
HUMAN_REJECTED
CASE_RESOLVED
```

Example:

```json
{
  "case_id": "CASE-18293",
  "event_type": "POLICY_CHECKED",
  "actor": "SAARTHI",
  "message": "Refund permitted under merchant policy",
  "result": "ALLOW",
  "timestamp": "..."
}
```

The audit trail must power the frontend timeline.

---

# 25. N8N INTEGRATION

Use n8n as the operational workflow automation layer.

Do not use n8n just for a trivial webhook.

Create meaningful workflows.

At minimum:

```text
01_case_memory_ingestion
02_scheduled_refund
03_proactive_settlement_monitor
04_failure_recovery
05_human_approval
```

---

# 26. N8N WORKFLOW — CASE MEMORY INGESTION

Trigger:

```text
case resolved
```

Flow:

```text
Webhook
↓
retrieve complete case
↓
construct memory document
↓
Cognee Add
↓
Cognee Cognify
```

Store:

- complaint
- diagnosis
- transaction context
- actions
- failures
- recovery
- resolution

This enables future cases to learn from historical work.

---

# 27. N8N WORKFLOW — SCHEDULED REFUND

Flow:

```text
Schedule / Wait
↓
GET settlement
↓
settlement completed?
     /       \
   YES        NO
    ↓          ↓
  done       check ETA
                ↓
          time threshold reached?
             /       \
           NO         YES
           ↓           ↓
         WAIT      call Saarthi
                       ↓
                  policy check
                       ↓
                  refund eligible?
                    /       \
                  YES        NO
                   ↓          ↓
                refund     escalate
                   ↓
                verify
```

Use n8n's waiting/scheduling capabilities where appropriate.

---

# 28. N8N WORKFLOW — PROACTIVE SETTLEMENT MONITOR

Periodically:

```text
Schedule Trigger
↓
find pending settlements
↓
find delayed settlements
↓
for each merchant
↓
ask Saarthi whether intervention is needed
↓
if needed:
    create case
    notify merchant
    add audit event
```

This demonstrates proactive operations.

The merchant does not have to open a ticket first.

---

# 29. N8N WORKFLOW — FAILURE RECOVERY

When an action fails:

```text
failure event
↓
retrieve current state
↓
did side effect happen?
 /              \
YES              NO
 |                |
update case       determine safe retry
                  ↓
                retry
                  ↓
               verify
                  ↓
          success / escalation
```

Ensure idempotency and state verification.

---

# 30. N8N WORKFLOW — HUMAN APPROVAL

Flow:

```text
Saarthi
↓
human approval required
↓
n8n webhook/workflow
↓
approval task
↓
React dashboard
↓
human decision
↓
callback/webhook
↓
Saarthi
↓
execute/reject/takeover
↓
verify
```

---

# 31. VOICE

Implement:

```text
POST /api/voice/transcribe
```

Accept audio.

Use Whisper.

Return:

```json
{
  "text": "My customer's payment failed but the money was deducted."
}
```

Then pass exactly that text to:

```text
POST /api/cases/{case_id}/message
```

The agent must not know whether the request originated from voice or text.

Add VAD if practical.

---

# 32. FRONTEND

Build a polished React merchant operations dashboard.

The dashboard is not a generic admin template.

It should visually communicate:

> What did Saarthi see?
>
> What did it remember?
>
> What did it reason?
>
> What was it authorized to do?
>
> What did it do?
>
> Did it work?
>
> What happened when it failed?
>
> Why did it escalate?

---

# 33. REQUIRED FRONTEND SCREENS

## Overview

Show:

```text
Cases handled today
Autonomously resolved
Human escalations
Autonomous resolution rate
Average time to meaningful action
Average resolution time
Recovery success rate
Actions completed
Human hours saved
Audit coverage
```

Clearly label simulated/demo metrics where appropriate.

---

# 34. CASE WORKSPACE

Layout:

```text
Merchant message
↓
Saarthi diagnosis
↓
Transaction context
↓
Policy status
↓
Current action
↓
Agent activity timeline
```

---

# 35. TRANSACTION CONTEXT PANEL

Show:

```text
MERCHANT
Urban Threads
Risk: LOW

TRANSACTION
TXN18293
₹3,200

PAYMENT
Customer debited
Payment pending

SETTLEMENT
Pending
ETA: 2h 15m

POLICY
Refund limit: ₹5,000
Eligibility: YES
```

---

# 36. MEMORY PANEL

Show retrieved Cognee context.

Example:

```text
🧠 SAARTHI MEMORY

3 similar historical cases found

CASE #17421
Settlement delay
Resolved automatically
1h 47m

CASE #16842
Payment pending
Settlement completed

Merchant history:
3 previous settlement-related cases
```

Clearly distinguish historical memory from current transaction state.

---

# 37. AGENT TIMELINE

Make this visually prominent.

Example:

```text
● New merchant case received

✓ Merchant identified

✓ Transaction retrieved

✓ Settlement checked

🧠 Similar historical cases retrieved

⚙ Issue classified: settlement delay

✓ Policy verified

⚙ Refund condition scheduled

✓ Case logged

✓ Settlement verified

✓ Merchant notified

✓ Case resolved
```

For failure:

```text
⚠ Refund API failed

⚙ Recovery initiated

✓ Refund state checked

⚙ Safe retry started

✓ Retry successful

✓ Refund verified

✓ Case resolved
```

---

# 38. HUMAN APPROVAL UI

Create a clear approval card:

```text
HUMAN APPROVAL REQUIRED

Case #18293

Refund requested:
₹15,000

Reason:
High-value product-quality dispute

Policy:
Autonomous refund limit exceeded

Completed checks:
✓ Transaction
✓ Dispute history
✓ Policy

Saarthi recommendation:
Review partial refund

[ APPROVE ]
[ REJECT ]
[ TAKE OVER ]
```

---

# 39. CHAT UI

Build merchant chat.

Example:

```text
Merchant:
My customer's payment failed but the money was deducted.

Saarthi:
I've checked transaction TXN18293.
The customer debit is confirmed, but the
payment is still pending settlement.
```

The interface should show real backend state and events rather than only fake conversational animation.

---

# 40. VOICE UI

Include:

```text
🎙 Speak to Saarthi
```

Display:

```text
Listening...
Transcribing...
```

Then show transcript and route it through the same case workflow.

---

# 41. PROACTIVE ALERT UI

Show something such as:

```text
⚠ PROACTIVE ALERT

Settlement delay detected

Merchant:
Urban Threads

Transaction:
TXN19931

Expected settlement:
2h ago

Saarthi has opened a monitoring case.
```

This demonstrates proactive operations.

---

# 42. REAL-TIME UPDATES

Prefer WebSockets if straightforward.

Otherwise implement polling.

Do not waste significant time debugging WebSockets.

The timeline should update as the backend agent progresses.

---

# 43. API CONTRACT

Create:

```text
/shared/api_contract.md
```

Document every endpoint and schema.

At minimum:

```text
POST /api/cases
GET /api/cases
GET /api/cases/{case_id}

POST /api/cases/{case_id}/message

GET /api/cases/{case_id}/timeline
GET /api/cases/{case_id}/context

GET /api/escalations
POST /api/escalations/{id}/approve
POST /api/escalations/{id}/reject
POST /api/escalations/{id}/takeover

GET /api/metrics

POST /api/voice/transcribe

POST /api/simulation/reset
POST /api/simulation/scenario/{scenario}
```

Use structured JSON.

Never make the frontend parse natural-language agent logs to infer state.

---

# 44. CASE RESPONSE SCHEMA

Use a structured response similar to:

```json
{
  "id": "CASE-18293",
  "status": "RECOVERING",
  "merchant_id": "M1001",
  "transaction_id": "TXN18293",
  "intent": "PAYMENT_DEBITED_PENDING",
  "diagnosis": {
    "root_cause": "SETTLEMENT_DELAY",
    "confidence": 0.94
  },
  "risk": "LOW",
  "current_action": {
    "type": "REFUND_RETRY",
    "status": "IN_PROGRESS"
  },
  "requires_human": false
}
```

---

# 45. EVENT SCHEMA

Example:

```json
{
  "id": "EVT-1007",
  "case_id": "CASE-18293",
  "type": "POLICY_CHECKED",
  "status": "SUCCESS",
  "timestamp": "2026-09-18T12:10:00Z",
  "message": "Refund permitted under merchant policy"
}
```

Use this for the frontend timeline.

---

# 46. METRICS

Implement backend metrics calculations.

Required metrics:

### Autonomous Resolution Rate

```text
eligible cases resolved without human intervention
/
eligible cases
```

### Time to First Meaningful Action

```text
case creation
→ first useful backend/workflow action
```

### Average Resolution Time

```text
case creation
→ verified resolution
```

### Escalation Rate

```text
escalated cases
/
total cases
```

### Recovery Rate

```text
failed actions recovered autonomously
/
failed actions
```

### First Contact Resolution

```text
cases resolved during initial autonomous workflow
/
eligible cases
```

### Human Hours Saved

Use an explicitly configurable demo estimate.

Do not present simulated estimates as validated real-world measurements.

### Action Success Rate

```text
actions resulting in intended state
/
actions attempted
```

### Audit Coverage

```text
cases with complete audit trail
/
total cases
```

---

# 47. SAFETY / TRUST MODEL

Implement:

## Least privilege

Agent only gets explicitly defined tools.

## Policy before action

Every side-effecting action requires policy evaluation.

## Verification before resolution

No action is considered successful until state is verified.

## Human control

High-risk / high-value / subjective actions require humans.

## Auditability

Every important action and decision is logged.

## Idempotency

Side effects must be protected against duplicate execution.

## State check before retry

Never blindly retry refunds.

## Transparent escalation

Every escalation has a concrete reason.

## No false claims

Never tell a merchant:

```text
"Your refund has completed"
```

unless backend verification confirms it.

---

# 48. MULTI-AGENT ARCHITECTURE

Do not make "six agents" the main product.

Use a supervisor plus specialized capabilities.

Potential internal components:

```text
Supervisor
├── Support/Diagnosis capability
├── Policy capability
├── Risk capability
├── Action Executor
├── Verification capability
└── Recovery capability
```

The supervisor owns the end-to-end objective.

Do not create unnecessary agent-to-agent complexity.

---

# 49. IMPORTANT PRODUCT BOUNDARIES

Do NOT build:

- generic FAQ chatbot as the primary product
- unrestricted LLM database access
- unrestricted payment/refund operations
- real payment integrations
- unnecessary sales platform
- full production authentication system unless needed
- Kubernetes
- complex microservice deployment
- unnecessary Redis infrastructure
- giant RAG corpus
- elaborate multi-agent communication
- fake projected business metrics presented as actual results

Focus on the autonomous merchant operations loop.

---

# 50. DEMO FLOW — PRIMARY SCENARIO

The final demo MUST support this exact story.

Merchant says:

> "My customer's payment failed, but the money was deducted."

Saarthi:

### Step 1 — Perceive

Identify merchant and transaction.

### Step 2 — Remember

Retrieve relevant historical cases from Cognee.

### Step 3 — Investigate

Retrieve:

- transaction state
- payment state
- customer debit
- settlement state
- settlement ETA
- relevant dispute information

### Step 4 — Reason

Determine:

```text
Payment isn't confirmed as failed.
Customer debit is confirmed.
Settlement is pending.
Likely cause = settlement delay.
```

### Step 5 — Policy

Check whether automated refund/scheduled refund is permitted.

### Step 6 — Act

Schedule appropriate refund workflow if permitted.

### Step 7 — n8n

Use n8n for the waiting/monitoring workflow.

### Step 8 — Verify

Check settlement state.

### Step 9 — Resolve

If settlement completes:

```text
✓ Merchant informed
✓ Case resolved
```

If settlement remains unresolved and refund condition is satisfied:

```text
execute refund
↓
verify refund
↓
resolve
```

---

# 51. DEMO FAILURE

Then deliberately trigger:

```text
Refund API → 500
```

Saarthi must show:

```text
Action failed
↓
Recovery started
↓
Checking current refund state
↓
Refund not processed
↓
Safe retry
↓
Retry succeeded
↓
Verification succeeded
↓
Resolved
```

This is essential.

---

# 52. DEMO ESCALATION

Then demonstrate:

Merchant:

> "The customer disputes the product quality and wants a ₹15,000 partial refund."

Saarthi:

```text
Subjective product-quality dispute
+
high-value refund
+
policy boundary
→ human approval
```

Show the complete approval workspace.

Do not autonomously refund.

Then approve through the UI:

```text
APPROVE
↓
controlled refund
↓
verification
↓
case resolved
```

---

# 53. DEMO PROACTIVE OPERATION

Show a pending settlement becoming delayed.

n8n:

```text
scheduled check
↓
delayed settlement detected
↓
Saarthi evaluates
↓
case created
↓
merchant notification
```

This demonstrates that Saarthi can operate proactively rather than merely waiting for a merchant complaint.

---

# 54. DEMO MEMORY

Demonstrate:

```text
"This happened again."
```

Cognee retrieves previous related cases.

Dashboard displays:

```text
Similar cases found:
3

Most relevant:
CASE #17421

Previous diagnosis:
Settlement delay

Previous resolution:
Settlement completed automatically
```

Saarthi uses this as context while still checking the current transaction state.

Historical memory must NEVER override current authoritative transaction state.

---

# 55. USER EXPERIENCE PRINCIPLE

The UI should make autonomous work observable.

A judge should immediately understand:

```text
INPUT
↓
CONTEXT
↓
MEMORY
↓
DIAGNOSIS
↓
POLICY
↓
ACTION
↓
VERIFICATION
↓
RECOVERY / ESCALATION
↓
OUTCOME
```

This is more important than visual decoration.

---

# 56. TESTING

Build tests for:

## Policy

```text
₹3,200 eligible refund → ALLOW
₹15,000 refund → APPROVAL
invalid state → DENY
product-quality dispute → APPROVAL
```

## Verification

```text
API success + actual refund completed → resolve
API success + refund still pending → do not resolve
```

## Recovery

```text
first refund attempt fails
second succeeds
→ resolve
```

## Escalation

```text
low confidence → escalate
high-value → escalate
subjective dispute → escalate
explicit human request → escalate
```

## Agent

Test structured diagnosis.

## API

Test all important endpoints.

## Frontend

Test key rendering/state flows where practical.

## End-to-end

At minimum:

```text
Scenario A → autonomous resolution
Scenario B → recovery
Scenario C → human escalation
```

---

# 57. DEVELOPMENT PRIORITY

Work in this order.

## P0 — MUST WORK

1. PostgreSQL
2. Database schema
3. Demo seed data
4. Simulated enterprise APIs
5. FastAPI
6. Agent supervisor
7. Diagnosis
8. Policy engine
9. Action executor
10. Verification
11. Escalation
12. React dashboard
13. Primary end-to-end flow

## P1 — IMPORTANT

14. Recovery
15. Audit timeline
16. Cognee
17. n8n scheduled refund
18. n8n proactive monitoring
19. Human approval
20. Voice
21. Metrics
22. Real-time updates

## P2 — ONLY IF TIME REMAINS

23. More sophisticated multi-agent decomposition
24. Advanced analytics
25. Redis
26. Additional proactive workflows
27. More advanced voice
28. Growth suggestions
29. Additional merchant workflows

Do not work on P2 while P0 is broken.

---

# 58. IMPLEMENTATION STRATEGY

Work incrementally.

After each major component:

1. Implement.
2. Run it.
3. Test it.
4. Fix errors.
5. Commit.
6. Continue.

Do not generate thousands of lines of untested code and only run it at the end.

Prefer small, composable modules.

---

# 59. ENVIRONMENT

Create `.env.example`.

Include variables such as:

```text
DATABASE_URL=
GEMINI_API_KEY=
GROQ_API_KEY=
LLM_PROVIDER=gemini

COGNEE_API_URL=
COGNEE_API_KEY=

N8N_BASE_URL=
N8N_WEBHOOK_URL=

WHISPER_MODEL=
```

Never commit real credentials.

Provide local-development fallback/mock behavior where possible.

---

# 60. DOCKER

Create a `docker-compose.yml` for local development.

At minimum support:

```text
postgres
backend
frontend
```

n8n can be included if practical.

If Cognee is cloud-hosted, configure it through environment variables rather than trying to self-host it unnecessarily.

---

# 61. README

Create a comprehensive README containing:

- product overview
- architecture
- setup
- environment variables
- database setup
- seed instructions
- backend startup
- frontend startup
- n8n setup/import
- Cognee setup
- demo scenarios
- API documentation
- testing
- architecture diagram
- safety model
- technology explanation
- limitations

Include a quick-start section so another person can clone and run the project.

---

# 62. ARCHITECTURE DOCUMENTATION

Document this conceptual architecture:

```text
Merchant
   ↓
Chat / Voice
   ↓
FastAPI
   ↓
Saarthi Supervisor
   ├── Current Context → PostgreSQL
   ├── Semantic Memory → Cognee
   ├── Policy → Policy Engine
   └── Tools → Enterprise APIs
              ↓
           Verify
              ↓
          Recover
              ↓
            n8n
              ↓
     Long-running automation
              ↓
        Merchant/Human
```

Explain:

```text
PostgreSQL = current transactional truth
Cognee = semantic memory
Saarthi = reasoning/decision layer
Policy Engine = authorization boundary
Enterprise APIs = controlled actions
Verification = outcome validation
Recovery = safe failure handling
n8n = operational workflow automation
React = operations cockpit
```

---

# 63. IMPORTANT ARCHITECTURAL RULE

The system should clearly separate:

## What is true now?

PostgreSQL.

## What happened before / what knowledge is relevant?

Cognee.

## What should Saarthi do?

Agent reasoning.

## Is it allowed?

Policy engine.

## How do we actually do it?

Controlled tools/APIs.

## Did it actually work?

Verification.

## What if it failed?

Recovery.

## What happens over time?

n8n.

This separation must remain visible in the code.

---

# 64. QUALITY BAR

The project should feel like a small but coherent enterprise product, not a collection of disconnected hackathon demos.

Prefer:

```text
one excellent autonomous workflow
```

over:

```text
ten half-working features
```

Every feature should connect to the central product thesis.

---

# 65. FINAL ACCEPTANCE CRITERIA

Do not consider the project complete until all of the following are possible.

### Scenario 1

A merchant submits:

```text
"My customer's payment failed but the money was deducted."
```

Saarthi:

```text
identifies transaction
→ retrieves current state
→ retrieves relevant memory
→ diagnoses settlement delay
→ checks policy
→ performs permitted action
→ uses n8n where appropriate
→ verifies state
→ communicates
→ resolves
```

### Scenario 2

Refund API fails.

Saarthi:

```text
detects failure
→ checks actual state
→ determines refund wasn't processed
→ retries safely
→ verifies
→ resolves
```

### Scenario 3

High-value subjective dispute.

Saarthi:

```text
recognizes subjective judgment
→ checks policy
→ refuses autonomous side effect
→ creates escalation
→ presents complete context
→ human approves/rejects/takes over
→ if approved, action occurs
→ outcome verified
```

### Scenario 4

Previously resolved similar case exists.

Saarthi:

```text
queries Cognee
→ retrieves historical context
→ uses it as supporting context
→ still validates current PostgreSQL state
```

### Scenario 5

Delayed settlement occurs without merchant complaint.

n8n:

```text
detects delay
→ calls Saarthi
→ creates/updates case
→ merchant is notified
```

---

# 66. FINAL DEMO NARRATIVE

The demo should communicate this sequence:

> A merchant reports a payment problem.

Saarthi sees the actual transaction.

It remembers related historical cases.

It investigates the current payment and settlement state.

It determines the actual issue.

It checks what it is authorized to do.

It takes the permitted action.

It does not assume that the action worked.

It verifies the actual business state.

When the action fails, it checks whether the operation nevertheless happened, safely recovers if possible, and only escalates when recovery is exhausted.

When a case requires human judgment, Saarthi recognizes that boundary and prepares the human with complete context instead of simply saying "I don't know."

n8n keeps long-running operational workflows moving.

Cognee gives Saarthi persistent operational memory.

The dashboard makes every meaningful step visible.

---

# 67. THE FINAL PRODUCT STATEMENT

Saarthi is:

> **An autonomous merchant operations teammate that understands a merchant's actual situation, remembers relevant operational history, investigates the underlying business state, makes policy-aware decisions, executes permitted actions, verifies the outcome, recovers when something fails, and knows when a human needs to take over.**

Core loop:

```text
PERCEIVE
   ↓
REMEMBER
   ↓
REASON
   ↓
CONTROL
   ↓
ACT
   ↓
VERIFY
   ↓
RECOVER
   ↓
ESCALATE
```

The central question the system should continuously ask is:

> **"What needs to be true for this merchant's problem to actually be solved?"**

Build the system around that question.

---

# 68. HOW YOU SHOULD OPERATE AS CLAUDE CODE

You are the primary implementation engineer.

Do not stop after creating a plan.

Inspect the repository first.

If an existing codebase exists, preserve useful existing work and integrate into it rather than unnecessarily rewriting everything.

If the repository is empty, create the complete project structure.

Make reasonable implementation decisions without asking unnecessary questions.

When a dependency or external service is unavailable:

- isolate it behind an interface
- provide a mock/local fallback
- continue implementing the rest of the system

Do not block the entire project because Cognee, n8n, Gemini, Groq, or another external service is temporarily unavailable.

Use environment variables for all credentials.

Never hardcode secrets.

Do not expose secrets in frontend code.

Keep the application runnable after each major milestone.

Prioritize the complete demo path.

When there is a choice between architectural elegance and a reliable hackathon demo, choose the reliable demo while keeping the code clean enough to explain.

Do not add unnecessary features merely because they are technically interesting.

---

# 69. IMMEDIATE EXECUTION PLAN

Start now.

## Phase 1

Inspect repository.

Then create:

```text
backend
frontend
shared
n8n
tests
```

Create environment configuration.

Create PostgreSQL schema.

Create deterministic demo data.

Create simulated enterprise APIs.

Make them runnable.

## Phase 2

Implement:

```text
agent state machine
supervisor
structured diagnosis
policy engine
action tools
verification
escalation
audit events
```

Get Scenario A working end-to-end.

## Phase 3

Implement:

```text
recovery
failure injection
Scenario B
Scenario C
human approval
```

## Phase 4

Implement:

```text
Cognee
memory retrieval
case memory ingestion
historical context
```

## Phase 5

Implement:

```text
n8n workflows
scheduled refund
settlement monitoring
memory ingestion
failure recovery
human approval
```

## Phase 6

Build the React dashboard and connect it to the real backend.

## Phase 7

Add voice.

## Phase 8

Add metrics.

## Phase 9

Run complete end-to-end tests.

## Phase 10

Polish the demo.

---

# 70. FINAL INSTRUCTION

Do not optimize this project for the number of technologies used.

Optimize it for one undeniable demonstration:

> **Saarthi receives a merchant problem, understands the real business state, remembers relevant history, reasons about the correct resolution, checks its authority, performs the work, verifies the result, recovers from failure, and escalates responsibly when it reaches a human boundary.**

Cognee and n8n should strengthen this story:

**Cognee gives Saarthi memory.**

**n8n gives Saarthi operational continuity.**

**The agent gives Saarthi reasoning.**

**The policy engine gives Saarthi boundaries.**

**Verification gives Saarthi trust.**

**Recovery gives Saarthi resilience.**

**The dashboard makes the autonomy observable.**

Build the complete system now.