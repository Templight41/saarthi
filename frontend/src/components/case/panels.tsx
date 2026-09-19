import { Brain, Repeat, ShieldCheck } from 'lucide-react'
import type { Case, CaseContext, WorkflowRun } from '../../types/api'
import { duration, humanise, rupees } from '../../lib/format'
import { Chip, Empty, Field, MicroLabel, Mono, Panel, decisionTone, riskTone } from '../ui'

export function DiagnosisCard({ kase }: { kase: Case }) {
  const d = kase.diagnosis
  if (!d) {
    return (
      <Panel title="Saarthi diagnosis">
        <Empty>Still investigating.</Empty>
      </Panel>
    )
  }
  const confidencePct = Math.round(d.confidence * 100)
  return (
    <Panel title="Saarthi diagnosis">
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone="acting">{d.root_cause}</Chip>
        <Chip>{d.intent}</Chip>
        <Chip tone={riskTone(d.risk)}>risk {d.risk}</Chip>
        {d.requires_human && <Chip tone="danger">needs a person</Chip>}
      </div>

      <p className="mt-3 text-[13px] leading-relaxed text-ink">{d.summary}</p>

      <div className="mt-3">
        <MicroLabel>Confidence</MicroLabel>
        <div className="flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-line">
            <div
              className={`h-full rounded-full ${confidencePct >= 60 ? 'bg-verified' : 'bg-danger'}`}
              style={{ width: `${confidencePct}%` }}
            />
          </div>
          <Mono className="text-[11px] text-ink-dim">{confidencePct}%</Mono>
        </div>
      </div>

      {d.evidence?.length > 0 && (
        <div className="mt-3">
          <MicroLabel>Evidence cited</MicroLabel>
          <ul className="space-y-0.5">
            {d.evidence.map((e) => (
              <li key={e} className="mono text-[11px] text-ink-dim">
                · {e}
              </li>
            ))}
          </ul>
        </div>
      )}

      {d.clamped_fields && d.clamped_fields.length > 0 && (
        <div className="mt-3 rounded-sm border border-acting/30 bg-acting/5 px-2.5 py-2">
          <MicroLabel>Corrected against live data</MicroLabel>
          <p className="text-[11px] leading-snug text-ink-dim">
            The model's answer was overridden on{' '}
            <Mono className="text-acting">{d.clamped_fields.join(', ')}</Mono> because the database
            said otherwise.
          </p>
        </div>
      )}
    </Panel>
  )
}

export function PolicyCard({ kase }: { kase: Case }) {
  const p = kase.policy
  return (
    <Panel title="Policy">
      {!p ? (
        <Empty>No authorisation decision yet.</Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheck size={14} className="text-ink-faint" />
            <Mono className="text-xs">{p.action}</Mono>
            <Chip tone={decisionTone(p.decision)}>{p.decision.replace('_', ' ')}</Chip>
          </div>
          <ul className="mt-2 space-y-1">
            {p.reasons.map((reason) => (
              <li key={reason} className="text-[12px] leading-snug text-ink-dim">
                {reason}
              </li>
            ))}
          </ul>
          {p.policy_ids.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {p.policy_ids.map((id) => (
                <Chip key={id} tone={id === 'APPROVED_BY_HUMAN' ? 'memory' : 'neutral'}>
                  {id}
                </Chip>
              ))}
            </div>
          )}
        </>
      )}
    </Panel>
  )
}

export function CurrentActionCard({ kase }: { kase: Case }) {
  const a = kase.current_action
  if (!a) return null
  return (
    <Panel title="Current action">
      <div className="flex flex-wrap items-center gap-2">
        <Mono className="text-xs">{a.type}</Mono>
        <Chip tone={a.status === 'FAILED' ? 'danger' : 'acting'}>{a.status}</Chip>
        {a.attempt > 1 && <Chip tone="danger">attempt {a.attempt}</Chip>}
      </div>
      {kase.wait_reason && (
        <p className="mt-2 text-[12px] text-ink-dim">
          Waiting on <Mono className="text-acting">{humanise(kase.wait_reason)}</Mono>. The standby
          refund stays armed until this resolves.
        </p>
      )}
    </Panel>
  )
}

export function TransactionContextPanel({ ctx }: { ctx: CaseContext }) {
  const txn = ctx.transaction
  const stl = ctx.settlement
  const eta = ctx.settlement_eta

  return (
    <Panel title="Transaction context">
      <div className="space-y-3.5">
        <Field label="Merchant">
          <div className="flex items-center gap-2">
            <span>{ctx.merchant.name}</span>
            <Chip tone={riskTone(ctx.merchant.risk_level)}>risk {ctx.merchant.risk_level}</Chip>
          </div>
          <Mono className="text-[11px] text-ink-faint">{ctx.merchant.id}</Mono>
        </Field>

        {txn ? (
          <>
            <Field label="Transaction">
              <Mono className="text-ink">{txn.id}</Mono>
              <div className="mono mt-0.5 text-[17px] font-semibold text-ink">{rupees(txn.amount)}</div>
              {txn.description && (
                <div className="text-[11px] text-ink-faint">{txn.description}</div>
              )}
            </Field>

            <Field label="Payment">
              <div className="flex flex-wrap items-center gap-2">
                <Chip tone={txn.payment_status === 'SUCCESS' ? 'verified' : 'acting'}>
                  {txn.payment_status}
                </Chip>
                {txn.customer_debited && <Chip tone="verified">customer debited</Chip>}
              </div>
            </Field>
          </>
        ) : (
          <Field label="Transaction">Not yet identified</Field>
        )}

        {stl && (
          <Field label="Settlement">
            <div className="flex flex-wrap items-center gap-2">
              <Chip tone={stl.status === 'COMPLETED' ? 'verified' : 'acting'}>{stl.status}</Chip>
              {eta?.eta_seconds != null && stl.status === 'PENDING' && (
                <Mono className="text-[11px] text-ink-dim">
                  {eta.overdue ? 'overdue by ' : 'ETA '}
                  {duration(Math.abs(eta.eta_seconds))}
                </Mono>
              )}
            </div>
            {stl.delay_reason && (
              <Mono className="text-[11px] text-ink-faint">{stl.delay_reason}</Mono>
            )}
          </Field>
        )}

        {ctx.disputes.length > 0 && (
          <Field label="Dispute">
            {ctx.disputes.map((d) => (
              <div key={d.id} className="mb-1">
                <div className="flex items-center gap-2">
                  <Chip tone="danger">{d.type}</Chip>
                  <Chip>{d.status}</Chip>
                </div>
                <p className="mt-1 text-[11px] leading-snug text-ink-dim">{d.description}</p>
                {d.requested_amount && (
                  <Mono className="text-[11px] text-ink-faint">
                    requested {rupees(d.requested_amount)}
                  </Mono>
                )}
              </div>
            ))}
          </Field>
        )}

        {ctx.refunds.length > 0 && (
          <Field label="Refunds">
            {ctx.refunds.map((r) => (
              <div key={r.id} className="flex items-center gap-2">
                <Mono className="text-[11px]">{r.id}</Mono>
                <Mono className="text-[11px]">{rupees(r.amount)}</Mono>
                <Chip
                  tone={
                    r.status === 'COMPLETED'
                      ? 'verified'
                      : r.status === 'CANCELLED'
                        ? 'neutral'
                        : 'acting'
                  }
                >
                  {r.status}
                </Chip>
                {r.attempt_count > 1 && <Chip tone="danger">{r.attempt_count} attempts</Chip>}
              </div>
            ))}
          </Field>
        )}

        <Field label="Policy">
          <div className="text-[12px] text-ink-dim">
            Autonomous refund limit{' '}
            <Mono className="text-ink">{rupees(ctx.merchant.autonomous_refund_limit)}</Mono>
          </div>
        </Field>
      </div>
    </Panel>
  )
}

export function PatternsPanel({ ctx }: { ctx: CaseContext }) {
  const patterns = ctx.patterns ?? []
  if (patterns.length === 0) return null

  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          <Repeat size={12} className="text-memory" />
          What keeps happening
        </span>
      }
      action={<span className="mono text-[10px] text-ink-faint">counted from the ledger</span>}
    >
      {/* Same violet treatment as memory, because this is history too. The
          difference from the panel above is how it was arrived at: these
          numbers were counted, not matched. */}
      <div className="mb-2 rounded-sm border border-memory/25 bg-memory/5 px-2 py-1">
        <span className="mono text-[9px] font-semibold tracking-wider text-memory uppercase">
          Merchant history — not current state
        </span>
      </div>

      <ul className="space-y-2.5">
        {patterns.map((pattern) => (
          <li key={pattern.pattern_type} className="border-l-2 border-memory/30 pl-2.5">
            <div className="flex items-baseline justify-between gap-2">
              <Mono className="text-[11px] text-memory">{humanise(pattern.pattern_type)}</Mono>
              <Chip tone={pattern.severity === 'HIGH' ? 'danger' : 'memory'}>
                {pattern.severity.toLowerCase()}
              </Chip>
            </div>
            <div className="text-[12px] text-ink">{pattern.summary}</div>
            <div className="text-[11px] leading-snug text-ink-dim">
              {pattern.recommended_attention}
            </div>
            <Mono className="text-[10px] text-ink-faint">
              {pattern.event_count} of {pattern.threshold} needed
              {pattern.related_transactions.length > 0 &&
                ` · ${pattern.related_transactions.slice(0, 3).join(', ')}`}
              {pattern.related_transactions.length > 3 && ' …'}
            </Mono>
          </li>
        ))}
      </ul>
    </Panel>
  )
}

export function MemoryPanel({ ctx }: { ctx: CaseContext }) {
  const memory = ctx.memory
  return (
    <Panel
      title={
        <span className="flex items-center gap-1.5">
          <Brain size={12} className="text-memory" />
          Saarthi memory
        </span>
      }
      action={
        memory && (
          <span className="mono text-[10px] text-ink-faint">
            via {memory.provider.replace('_', ' ')}
            {memory.degraded && ' · degraded'}
          </span>
        )
      }
    >
      {!memory || memory.similar_cases.length === 0 ? (
        <Empty>No similar cases found.</Empty>
      ) : (
        <>
          {/* Historical context is visually distinct from live state, because
              it must never be mistaken for what is true right now. */}
          <div className="mb-2 rounded-sm border border-memory/25 bg-memory/5 px-2 py-1">
            <span className="mono text-[9px] font-semibold tracking-wider text-memory uppercase">
              Historical — not current state
            </span>
          </div>

          <p className="mb-3 text-[12px] text-ink-dim">
            <Mono className="text-memory">{memory.similar_cases.length}</Mono> similar historical
            case{memory.similar_cases.length === 1 ? '' : 's'} found
          </p>

          <ul className="space-y-2.5">
            {memory.similar_cases.map((hit) => (
              <li key={hit.doc_id} className="border-l-2 border-memory/30 pl-2.5">
                <div className="flex items-baseline justify-between gap-2">
                  <Mono className="text-[11px] text-memory">{hit.case_id}</Mono>
                  <Mono className="text-[10px] text-ink-faint">
                    {Math.round(hit.similarity * 100)}% match
                  </Mono>
                </div>
                <div className="text-[12px] text-ink">{humanise(hit.diagnosis)}</div>
                {hit.resolution && (
                  <div className="text-[11px] leading-snug text-ink-dim">{hit.resolution}</div>
                )}
                {hit.resolution_time_seconds != null && (
                  <Mono className="text-[10px] text-ink-faint">
                    resolved in {duration(hit.resolution_time_seconds)}
                  </Mono>
                )}
              </li>
            ))}
          </ul>

          {memory.merchant_history && (
            <p className="mt-3 border-t border-line pt-2.5 text-[11px] text-ink-dim">
              Merchant history:{' '}
              <Mono className="text-ink">{memory.merchant_history.previous_case_count}</Mono>{' '}
              previously resolved case
              {memory.merchant_history.previous_case_count === 1 ? '' : 's'}
              <span className="mono ml-1 text-ink-faint">(from the database, not memory)</span>
            </p>
          )}
        </>
      )}
    </Panel>
  )
}

export function WorkflowRunsCard({ runs }: { runs: WorkflowRun[] }) {
  if (!runs.length) return null
  return (
    <Panel title="Workflows">
      <ul className="space-y-2">
        {runs.map((run) => (
          <li key={run.id} className="flex flex-wrap items-center gap-2">
            <Mono className="text-[11px] text-ink">{run.workflow.replace(/_/g, ' ')}</Mono>
            <Chip tone={run.engine === 'n8n' ? 'memory' : 'neutral'}>{run.engine}</Chip>
            <Chip tone={run.status === 'COMPLETED' ? 'verified' : 'acting'}>{run.status}</Chip>
            {run.attempts > 0 && (
              <Mono className="text-[10px] text-ink-faint">{run.attempts} checks</Mono>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  )
}
