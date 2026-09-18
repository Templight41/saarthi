import { useState } from 'react'
import { Check, UserCog, X } from 'lucide-react'
import type { Escalation } from '../../types/api'
import { humanise, rupees } from '../../lib/format'
import { useDecision } from '../../hooks/queries'
import { Button, Chip, MicroLabel, Mono, riskTone } from '../ui'

export function ApprovalCard({ escalation }: { escalation: Escalation }) {
  const [note, setNote] = useState('')
  const decision = useDecision()
  const pending = escalation.status === 'PENDING_HUMAN'
  const busy = decision.isPending

  return (
    <section className="panel border-danger/40">
      <header className="flex items-center justify-between border-b border-danger/30 bg-danger/10 px-4 py-2.5">
        <div className="mono text-[11px] font-semibold tracking-wider text-danger uppercase">
          Human approval required
        </div>
        <Mono className="text-[11px] text-ink-dim">{escalation.case_id}</Mono>
      </header>

      <div className="space-y-3.5 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone="danger">{humanise(escalation.reason)}</Chip>
          {escalation.risk && <Chip tone={riskTone(escalation.risk)}>risk {escalation.risk}</Chip>}
          {!pending && <Chip>{escalation.status}</Chip>}
        </div>

        {escalation.amount && (
          <div>
            <MicroLabel>Refund requested</MicroLabel>
            <div className="mono text-2xl font-semibold">{rupees(escalation.amount)}</div>
          </div>
        )}

        {escalation.case_summary && (
          <div>
            <MicroLabel>Situation</MicroLabel>
            <p className="text-[13px] leading-relaxed text-ink">{escalation.case_summary}</p>
          </div>
        )}

        {escalation.policy && (
          <div>
            <MicroLabel>Policy position</MicroLabel>
            <ul className="space-y-0.5">
              {escalation.policy.reasons.map((reason) => (
                <li key={reason} className="text-[12px] leading-snug text-ink-dim">
                  {reason}
                </li>
              ))}
            </ul>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {escalation.policy.policy_ids.map((id) => (
                <Chip key={id}>{id}</Chip>
              ))}
            </div>
          </div>
        )}

        {escalation.completed_actions.length > 0 && (
          <div>
            <MicroLabel>Already checked</MicroLabel>
            <ul className="space-y-0.5">
              {escalation.completed_actions.map((action) => (
                <li key={action} className="flex items-center gap-1.5 text-[12px] text-ink-dim">
                  <Check size={11} className="text-verified" strokeWidth={3} />
                  {action}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="rounded-sm border border-line bg-panel-raised px-3 py-2">
          <MicroLabel>Saarthi recommends</MicroLabel>
          <div className="mono text-[12px] text-ink">{humanise(escalation.recommendation)}</div>
          {escalation.pending_action && (
            <div className="mono mt-1 text-[11px] text-ink-faint">
              awaiting approval to run {escalation.pending_action.tool}
            </div>
          )}
        </div>

        {pending ? (
          <>
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="Optional note for the merchant"
              className="w-full rounded-sm border border-line bg-ground px-2.5 py-1.5 text-[12px] text-ink outline-none placeholder:text-ink-faint focus:border-line-bright"
            />
            <div className="flex flex-wrap gap-2">
              <Button
                tone="primary"
                disabled={busy}
                onClick={() =>
                  decision.mutate({ id: escalation.id, decision: 'approve', note: note || undefined })
                }
              >
                <span className="flex items-center gap-1.5">
                  <Check size={12} strokeWidth={3} /> Approve
                </span>
              </Button>
              <Button
                tone="danger"
                disabled={busy}
                onClick={() =>
                  decision.mutate({ id: escalation.id, decision: 'reject', note: note || undefined })
                }
              >
                <span className="flex items-center gap-1.5">
                  <X size={12} strokeWidth={3} /> Reject
                </span>
              </Button>
              <Button
                disabled={busy}
                onClick={() => decision.mutate({ id: escalation.id, decision: 'takeover' })}
              >
                <span className="flex items-center gap-1.5">
                  <UserCog size={12} /> Take over
                </span>
              </Button>
            </div>
          </>
        ) : (
          escalation.human_decision && (
            <div className="text-[12px] text-ink-dim">
              <Mono className="text-ink">{escalation.decided_by}</Mono> chose{' '}
              <Mono>{escalation.human_decision.decision}</Mono>
              {escalation.human_decision.note && ` — ${escalation.human_decision.note}`}
            </div>
          )
        )}
      </div>
    </section>
  )
}
