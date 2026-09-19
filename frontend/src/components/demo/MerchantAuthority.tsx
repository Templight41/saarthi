import { useState } from 'react'
import { ShieldCheck } from 'lucide-react'
import { api } from '../../services/api'
import { useMerchants, useRefreshAll } from '../../hooks/queries'
import { Button, Chip, MicroLabel, Mono } from '../ui'
import { rupees } from '../../lib/format'

/**
 * How much Saarthi may refund for each merchant without asking a person.
 *
 * This is the line between an autonomous action and an escalation, and it is
 * genuinely per-merchant: five years of steady volume is not the same risk as
 * an account opened last week. Lower it below a pending refund and that refund
 * stops and asks; raise it and the same refund goes through — the policy
 * engine re-reads the merchant on every decision, so there is nothing to
 * invalidate.
 *
 * Every change is attributed and kept. A limit with no history cannot be
 * reviewed afterwards, and this one decides how much money moves unattended.
 */
export function MerchantAuthority({ changedBy = 'ops@urbanthreads.in' }: { changedBy?: string }) {
  const { data } = useMerchants()
  const refresh = useRefreshAll()
  const [editing, setEditing] = useState<string | null>(null)
  const [limit, setLimit] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const ceiling = data?.max_autonomous_refund_limit

  function open(merchantId: string, current: string) {
    setEditing(merchantId === editing ? null : merchantId)
    setLimit(current)
    setReason('')
    setNote(null)
  }

  async function save(merchantId: string) {
    setBusy(true)
    setNote(null)
    try {
      const result = await api.setRefundLimit(merchantId, limit, changedBy, reason)
      setNote(
        result.changed
          ? `${merchantId} can now be refunded up to ₹${result.merchant.autonomous_refund_limit} unattended`
          : 'That is already the limit',
      )
      setEditing(null)
      refresh()
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not change it')
    } finally {
      setBusy(false)
    }
  }

  const field =
    'w-full rounded-sm border border-line bg-ground px-2 py-1.5 text-[12px] text-ink outline-none focus:border-line-bright'

  return (
    <div>
      <div className="flex items-baseline justify-between">
        <MicroLabel>Refund authority</MicroLabel>
        {ceiling && (
          <Mono className="text-[10px] text-ink-faint">ceiling ₹{ceiling}</Mono>
        )}
      </div>
      <p className="mb-2 text-[11px] leading-snug text-ink-faint">
        How much Saarthi may refund without asking. Anything above it escalates.
      </p>

      <div className="space-y-1.5">
        {(data?.merchants ?? []).map((m) => (
          <div key={m.id} className="rounded-sm border border-line bg-panel-raised px-2.5 py-2">
            <div className="flex items-center gap-2">
              <ShieldCheck size={12} className="text-verified" />
              <span className="text-[12px] text-ink">{m.name}</span>
              <Mono className="text-[10px] text-ink-faint">{m.id}</Mono>
              <Chip tone={m.risk_level === 'LOW' ? 'verified' : 'acting'}>
                {m.risk_level.toLowerCase()}
              </Chip>
              <Mono className="ml-auto text-[11px] text-ink">
                {rupees(m.autonomous_refund_limit)}
              </Mono>
              <button
                type="button"
                onClick={() => open(m.id, m.autonomous_refund_limit)}
                className="text-[10px] text-ink-faint underline-offset-2 hover:text-ink-dim hover:underline"
              >
                {editing === m.id ? 'cancel' : 'change'}
              </button>
            </div>

            {m.limit_changed_at && (
              <Mono className="mt-0.5 block text-[10px] text-ink-faint">
                last changed by {m.limit_changed_by}
                {m.limit_history.length > 0 &&
                  ` · ${m.limit_history[m.limit_history.length - 1].from} → ${
                    m.limit_history[m.limit_history.length - 1].to
                  }`}
              </Mono>
            )}

            {editing === m.id && (
              <div className="mt-2 space-y-2 border-t border-line pt-2">
                <div className="grid grid-cols-2 gap-2">
                  <label className="block">
                    <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
                      New limit
                    </span>
                    <input
                      className={field}
                      value={limit}
                      onChange={(e) => setLimit(e.target.value)}
                      aria-label={`New refund limit for ${m.id}`}
                    />
                  </label>
                  <label className="block">
                    <span className="mono text-[9px] tracking-wider text-ink-faint uppercase">
                      Why
                    </span>
                    <input
                      className={field}
                      placeholder="Five years, steady volume"
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      aria-label={`Reason for changing ${m.id}`}
                    />
                  </label>
                </div>
                <Button tone="primary" onClick={() => save(m.id)} disabled={busy || !limit.trim()}>
                  Change authority
                </Button>
                <p className="text-[10px] leading-snug text-ink-faint">
                  Recorded against {changedBy}. Resetting the fixtures restores the seeded limit.
                </p>
              </div>
            )}
          </div>
        ))}
      </div>

      {note && <div className="mono mt-2 text-[10px] text-ink-faint">{note}</div>}
    </div>
  )
}
