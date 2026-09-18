import type { Case, CaseStatus } from '../types/api'

/** The specification's core loop, made literal at the top of every case. */
export const STAGES = [
  { key: 'PERCEIVE', label: 'Perceive', statuses: ['RECEIVED', 'IDENTIFYING'] },
  { key: 'REMEMBER', label: 'Remember', statuses: ['INVESTIGATING'] },
  { key: 'REASON', label: 'Reason', statuses: ['DIAGNOSING'] },
  { key: 'CONTROL', label: 'Control', statuses: ['POLICY_CHECK', 'PLANNING'] },
  { key: 'ACT', label: 'Act', statuses: ['ACTING'] },
  { key: 'VERIFY', label: 'Verify', statuses: ['VERIFYING'] },
  { key: 'RECOVER', label: 'Recover', statuses: ['RECOVERING'] },
  { key: 'OUTCOME', label: 'Outcome', statuses: ['RESOLVED', 'ESCALATED'] },
] as const

export type StageState = 'done' | 'active' | 'pending' | 'skipped' | 'failed'

const ORDER: CaseStatus[] = [
  'RECEIVED',
  'IDENTIFYING',
  'INVESTIGATING',
  'DIAGNOSING',
  'POLICY_CHECK',
  'PLANNING',
  'ACTING',
  'VERIFYING',
  'RECOVERING',
  'RESOLVED',
  'ESCALATED',
]

export function stageStates(kase: Case, seenStatuses: Set<string>): Record<string, StageState> {
  const out: Record<string, StageState> = {}
  const currentIndex = ORDER.indexOf(kase.status)
  const terminal = kase.status === 'RESOLVED' || kase.status === 'ESCALATED'

  for (const stage of STAGES) {
    const isCurrent = (stage.statuses as readonly string[]).includes(kase.status)
    const wasVisited = stage.statuses.some((s) => seenStatuses.has(s))

    if (stage.key === 'OUTCOME') {
      out[stage.key] = terminal ? (kase.status === 'ESCALATED' ? 'failed' : 'done') : 'pending'
      continue
    }
    if (stage.key === 'RECOVER' && !wasVisited) {
      // Recovery only appears when something actually went wrong.
      out[stage.key] = isCurrent ? 'active' : 'skipped'
      continue
    }
    if (isCurrent && !terminal) {
      out[stage.key] = 'active'
      continue
    }
    const stageIndex = Math.min(...stage.statuses.map((s) => ORDER.indexOf(s as CaseStatus)))
    out[stage.key] = wasVisited || stageIndex < currentIndex || terminal ? 'done' : 'pending'
  }
  return out
}

export function statusesFromEvents(events: { type: string; metadata?: any }[]): Set<string> {
  const seen = new Set<string>()
  for (const event of events) {
    if (event.type === 'STATE_CHANGED' && event.metadata?.to) seen.add(event.metadata.to)
    if (event.type === 'STATE_CHANGED' && event.metadata?.from) seen.add(event.metadata.from)
  }
  return seen
}
