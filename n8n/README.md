# n8n workflows

n8n schedules and waits. The backend owns every decision and every side effect. Each node that does
something is an HTTP call to `/api/internal/...`, which invokes the same step function the in-process
engine calls directly — so switching engines cannot change behaviour, only who does the waiting.

## Setup

```bash
make n8n-up      # starts n8n at http://localhost:5678
make n8n-import  # imports every workflow in this directory
```

Create the owner account on first visit. Then add a **Header Auth** credential named
`Saarthi Internal Token` with header `X-Saarthi-Internal-Token` and the value of `INTERNAL_API_TOKEN`
from your `.env`, and activate the workflows.

Finally set `WORKFLOW_ENGINE=n8n` and restart the backend.

If the CLI import gives trouble, import each JSON file through the UI instead: **Workflows → Import
from file**.

## The workflows

| File | Trigger | What it does |
|---|---|---|
| `01_case_memory_ingestion.json` | webhook on resolution | Build the case memory document and write it to the store |
| `02_scheduled_refund.json` | webhook, then Wait loop | Check settlement, stand the refund down if it lands, execute it if the deadline passes |
| `03_settlement_monitor.json` | every minute, plus run-now | Find overdue settlements with no open case and open one |
| `04_failure_recovery.json` | webhook on failure | Back off and ask the backend whether a retry is safe |
| `05_human_approval.json` | webhook, then Wait on webhook | Notify a reviewer, remind every ten minutes, resume on the decision |

## Networking

The backend normally runs on the host while n8n runs in Docker, so `SAARTHI_API_BASE` defaults to
`http://host.docker.internal:8000`. If you run the backend in Compose too, set it to
`http://backend:8000`.

## If n8n is unavailable

Nothing breaks. A failed webhook dispatch falls back to the in-process engine for that individual run
and records why on the timeline. `WORKFLOW_ENGINE=local` switches everything back, and the badge in
the dashboard header always shows which engine is actually live.
