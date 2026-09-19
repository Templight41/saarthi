# Five minutes

Press **Demo** in the top bar. Every scenario is one click: it restores the fixtures, arms exactly
one condition and starts the case the way it would really start. Run them in this order.

The line to keep coming back to: *a tool returning success is not evidence that anything happened.*
Three of these end with Saarthi doing the work. Two end with it stopping, and the stopping is the
part worth watching.

---

## 1 · Settlement delay — 60 seconds

> "My customer's payment failed, but the money was deducted."

The merchant is wrong, and Saarthi says so gently. The ledger has the payment as **pending**, not
failed.

Watch for **`clamped_fields`** on the diagnosis card. The model called this a confirmed failure;
the database disagreed and the database won. The correction is recorded rather than hidden — live
models really do get this one wrong.

It schedules a **standby refund** so the customer is made whole if settlement never lands, tells
the merchant the real position, and then **parks**. It has not resolved anything, because it does
not know yet.

Now press **Complete TXN18293**. It verifies the settlement by re-reading it, stands the standby
refund down, and resolves. Press **Fail TXN18293** instead and the same case refunds the customer.

## 2 · Refund failure — 45 seconds

> "The customer cancelled order A-5521 and wants the ₹2,500 refunded."

The gateway fails on the first attempt. The interesting question is not whether Saarthi retries.

Follow the timeline: `ACTION_FAILED` is followed by a **lookup, not a retry**. It asks whether the
refund landed anyway, by its idempotency key. Only once it can prove nothing happened does it try
again — with the same key, so a duplicate is impossible by construction rather than by discipline.

Check the transaction panel at the end: **one refund, two attempts, one key**.

## 3 · High-value dispute — 60 seconds

> "The customer says the product quality was poor and wants a ₹15,000 partial refund."

₹15,000 against a ₹5,000 limit, over a question of quality. Two independent reasons to stop, and
Saarthi names both.

It refuses, and escalates with the transaction, the dispute, the policy position, the checks it has
already completed and a recommendation. Open **Escalations**.

Press **Approve**. The refund runs through the **same executor and the same verifier** as any
autonomous action, with the override recorded in the audit trail. **Reject** and **Take over** both
work too.

This case is excluded from the autonomy-rate denominator. Counting it as a failure would punish
exactly the behaviour we want.

## 4 · Soundbox mismatch — 60 seconds

> "My Soundbox said payment received, but I don't see the payment in my dashboard."

A Soundbox is the speaker on the counter, and it is the most trusted signal in a small merchant's
day. Three announcements came from that device in the last hour, and they sounded identical.

Open **What the device announced**:

- **₹240** — the ledger confirms it. Real payment; the dashboard was the problem.
- **₹180** — real, but the bank has not confirmed it yet.
- **₹500** — *the ledger has never heard of it.*

Saarthi escalates rather than resolving. There is no payment to credit, refund or apologise for,
and every autonomous option here begins by assuming there is one. A model asked to be helpful will
invent that payment; the announcement and the ledger are stored as different kinds of thing
precisely so that it cannot.

## 5 · Proactive anomaly — 45 seconds

Nobody says anything.

A settlement is two hours past its window, the monitor notices, and a case opens with origin
**PROACTIVE**. Check the conversation panel: there is **no inbound merchant message at all**.

It then runs the ordinary pipeline — same investigation, same policy engine, same verification.
There is no second agent for proactive work.

> The merchant never contacted Saarthi. Saarthi found it first.

## 6 · The numbers — 30 seconds

Overview. Cases, autonomous resolutions, escalations, recovery success. Estimated figures are
labelled as estimates, and the cases that escalated *because policy required a person* are excluded
from the autonomy rate.

## 7 · The architecture — 60 seconds

`docs/PRODUCTION.md` lists what a real payment-platform integration would require: authentication,
tenant isolation, scoped credentials, idempotency across processes, DPDP retention, authenticated
approvers. None of it is implemented, and it says so.

What *is* built is the seam — `services/providers.py` — and a test that the current services still
satisfy it. Swapping in a real platform means writing new adapters. It does not mean touching the
agent, the policy engine or the verifier.

---

## If something looks wrong

`make check-providers` shows what is actually live; `make check-voice` makes the speech provider
actually speak. Any scenario can be put back with one call:

```bash
curl -XPOST localhost:8000/api/scenarios/settlement_delay/reset
curl -s localhost:8000/api/scenarios/settlement_delay/status | python3 -m json.tool
```

`status` reads the checkpoints off the audit trail, so it tells you what really happened rather
than what the scenario intended.
