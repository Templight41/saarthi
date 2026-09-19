import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { CheckCircle2, PlayCircle, RotateCcw, Siren, XCircle, Zap } from 'lucide-react'
import { api } from '../../services/api'
import { useRefreshAll, useScenarios } from '../../hooks/queries'
import { Button, Chip, MicroLabel, Mono } from '../ui'

/**
 * How a presenter actually drives the demo. Every control is deterministic:
 * reset restores the same fixtures, scenarios arm the same conditions, and
 * settlement can be completed or failed on command.
 */
export function DemoConsole({ onDone }: { onDone?: () => void }) {
  const { data: scenarios } = useScenarios()
  const refresh = useRefreshAll()
  const navigate = useNavigate()
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function run(label: string, fn: () => Promise<unknown>, message?: string) {
    setBusy(label)
    setNote(null)
    try {
      await fn()
      setNote(message ?? `${label} done`)
      refresh()
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Something went wrong')
    } finally {
      setBusy(null)
    }
  }

  /**
   * One call. The lab resets the fixtures, arms the scenario and starts it the
   * way it would really start — as a merchant message, or as nothing at all
   * for the proactive one, where the monitor is left to notice by itself.
   */
  async function runScenario(id: string) {
    setBusy(id)
    setNote(null)
    try {
      const started = await api.startScenario(id)
      refresh()
      if (started.case_id) {
        onDone?.()
        navigate(`/cases/${started.case_id}`)
      } else {
        setNote('The monitor ran and found nothing due.')
      }
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Scenario failed to start')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <MicroLabel>Run a scenario</MicroLabel>
        <div className="space-y-2">
          {scenarios?.map((s, index) => (
            <button
              key={s.id}
              type="button"
              disabled={busy !== null}
              onClick={() => runScenario(s.id)}
              className="w-full rounded-sm border border-line bg-panel-raised px-3 py-2.5 text-left transition-colors hover:border-line-bright disabled:opacity-50"
            >
              <div className="flex items-center gap-2">
                {s.trigger === 'MONITOR' ? (
                  <Siren size={13} className="text-acting" />
                ) : (
                  <PlayCircle size={13} className="text-acting" />
                )}
                <span className="text-[13px] font-medium text-ink">
                  {index + 1}. {s.name}
                </span>
                <Mono className="ml-auto text-[10px] text-ink-faint">
                  {s.transaction_id ?? s.merchant_id}
                </Mono>
              </div>
              <p className="mt-1 text-[11px] leading-snug text-ink-dim">{s.capability}</p>
              {/* The most useful line on the card: where it stops. */}
              <p className="mt-1 text-[11px] leading-snug text-ink-faint">
                {s.autonomy_boundary}
              </p>
            </button>
          ))}
        </div>
      </div>

      <div>
        <MicroLabel>Drive the settlement</MicroLabel>
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() =>
              run('complete', () => api.completeSettlement('TXN18293'), 'Settlement completed')
            }
            disabled={busy !== null}
          >
            <span className="flex items-center gap-1.5">
              <CheckCircle2 size={12} /> Complete TXN18293
            </span>
          </Button>
          <Button
            tone="danger"
            onClick={() => run('fail', () => api.failSettlement('TXN18293'), 'Settlement failed')}
            disabled={busy !== null}
          >
            <span className="flex items-center gap-1.5">
              <XCircle size={12} /> Fail TXN18293
            </span>
          </Button>
        </div>
      </div>

      <div>
        <MicroLabel>Inject a failure</MicroLabel>
        <Button
          onClick={() =>
            run('arm', () => api.armRefundFailure(1), 'Next refund attempt will fail once')
          }
          disabled={busy !== null}
        >
          <span className="flex items-center gap-1.5">
            <Zap size={12} className="text-acting" /> Fail the next refund once
          </span>
        </Button>
      </div>

      <div className="border-t border-line pt-4">
        <Button
          tone="ghost"
          onClick={() => run('reset', api.reset, 'Everything reset to the seeded fixtures')}
          disabled={busy !== null}
        >
          <span className="flex items-center gap-1.5">
            <RotateCcw size={12} /> Reset all demo data
          </span>
        </Button>
      </div>

      {note && (
        <div className="rounded-sm border border-line bg-panel-raised px-2.5 py-1.5">
          <Chip tone="verified">{note}</Chip>
        </div>
      )}
    </div>
  )
}
