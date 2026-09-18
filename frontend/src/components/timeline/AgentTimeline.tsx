import { Brain, Check, Circle, Cog, Radar, TriangleAlert, UserCheck } from 'lucide-react'
import type { AgentEvent } from '../../types/api'
import { markFor, offsetSeconds, toneClass, type Glyph } from '../../lib/timeline'
import { clock, offsetLabel } from '../../lib/format'
import { Empty } from '../ui'

const ICONS: Record<Glyph, typeof Check> = {
  dot: Circle,
  check: Check,
  brain: Brain,
  cog: Cog,
  warn: TriangleAlert,
  radar: Radar,
  human: UserCheck,
}

const ACTOR_LABEL: Record<string, string> = {
  SAARTHI: 'SAARTHI',
  MERCHANT: 'MERCHANT',
  HUMAN: 'HUMAN',
  SYSTEM: 'SYSTEM',
  N8N: 'N8N',
  WORKFLOW: 'LOCAL WF',
}

export function AgentTimeline({ events, live }: { events: AgentEvent[]; live: boolean }) {
  if (!events.length) return <Empty>No activity recorded yet.</Empty>

  return (
    <ol className="relative">
      {events.map((event, index) => {
        const mark = markFor(event)
        const Icon = ICONS[mark.glyph]
        const isLatest = index === events.length - 1
        const highlight = isLatest && live

        return (
          <li key={event.id} className="relative flex gap-3 pb-3 last:pb-0">
            {index < events.length - 1 && (
              <div className="absolute left-[11px] top-6 bottom-0 w-px bg-line" aria-hidden />
            )}
            <div
              className={[
                'relative z-10 mt-0.5 flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full border bg-panel',
                highlight ? 'border-acting pulse' : 'border-line',
                toneClass[mark.tone],
              ].join(' ')}
            >
              <Icon
                size={mark.glyph === 'dot' ? 7 : 12}
                strokeWidth={mark.glyph === 'check' ? 3 : 2}
                fill={mark.glyph === 'dot' ? 'currentColor' : 'none'}
                className={mark.spinning ? 'spin-slow' : undefined}
                aria-hidden
              />
            </div>

            <div className="min-w-0 flex-1 pt-px">
              <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className={`mono text-[10px] font-semibold tracking-wide ${toneClass[mark.tone]}`}>
                  {event.type}
                </span>
                <span className="mono text-[10px] text-ink-faint">
                  {ACTOR_LABEL[event.actor] ?? event.actor}
                </span>
                <span className="mono text-[10px] text-ink-faint">
                  {offsetLabel(offsetSeconds(event, events))}
                </span>
                <span className="mono text-[10px] text-ink-faint/60">{clock(event.timestamp)}</span>
              </div>
              <p className="mt-0.5 text-[13px] leading-snug text-ink">{event.message}</p>
            </div>
          </li>
        )
      })}
    </ol>
  )
}
