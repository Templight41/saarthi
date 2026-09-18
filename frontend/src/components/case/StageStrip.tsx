import { Check, Circle, X } from 'lucide-react'
import type { AgentEvent, Case } from '../../types/api'
import { STAGES, stageStates, statusesFromEvents } from '../../lib/stages'

/**
 * The specification's core loop rendered literally, so a judge can see at a
 * glance where the agent is and what it has already done.
 */
export function StageStrip({ kase, events }: { kase: Case; events: AgentEvent[] }) {
  const states = stageStates(kase, statusesFromEvents(events))

  return (
    <div className="panel px-4 py-3">
      <div className="flex items-center gap-1 overflow-x-auto">
        {STAGES.map((stage, index) => {
          const state = states[stage.key]
          const isLast = index === STAGES.length - 1
          return (
            <div key={stage.key} className="flex flex-1 items-center gap-1 min-w-[84px]">
              <div className="flex flex-1 flex-col items-center gap-1.5">
                <div
                  className={[
                    'flex h-6 w-6 items-center justify-center rounded-full border transition-colors',
                    state === 'done' && 'border-verified/60 bg-verified/15 text-verified',
                    state === 'active' && 'border-acting bg-acting/20 text-acting pulse',
                    state === 'failed' && 'border-danger/60 bg-danger/15 text-danger',
                    state === 'pending' && 'border-line text-ink-faint',
                    state === 'skipped' && 'border-line/50 text-ink-faint/40',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  {state === 'done' && <Check size={13} strokeWidth={3} />}
                  {state === 'failed' && <X size={13} strokeWidth={3} />}
                  {(state === 'active' || state === 'pending' || state === 'skipped') && (
                    <Circle size={7} fill="currentColor" strokeWidth={0} />
                  )}
                </div>
                <div
                  className={[
                    'mono text-[9px] font-semibold tracking-wider uppercase whitespace-nowrap',
                    state === 'active' && 'text-acting',
                    state === 'done' && 'text-ink-dim',
                    state === 'failed' && 'text-danger',
                    (state === 'pending' || state === 'skipped') && 'text-ink-faint/60',
                  ]
                    .filter(Boolean)
                    .join(' ')}
                >
                  {stage.label}
                </div>
              </div>
              {!isLast && (
                <div
                  className={`h-px flex-1 ${
                    states[STAGES[index + 1].key] === 'pending' ? 'bg-line' : 'bg-line-bright'
                  }`}
                />
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
