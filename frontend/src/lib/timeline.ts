import type { AgentEvent, EventStatus } from '../types/api'

export type Glyph = 'dot' | 'check' | 'brain' | 'cog' | 'warn' | 'radar' | 'human'
export type Tone = 'neutral' | 'verified' | 'acting' | 'memory' | 'danger' | 'caution'

export interface TimelineMark {
  glyph: Glyph
  tone: Tone
  spinning: boolean
}

const VERIFIED_EVENTS = new Set([
  'MERCHANT_IDENTIFIED',
  'TRANSACTION_IDENTIFIED',
  'TRANSACTION_RETRIEVED',
  'SETTLEMENT_CHECKED',
  'DISPUTE_CHECKED',
  'ACTION_COMPLETED',
  'ACTION_VERIFIED',
  'SETTLEMENT_VERIFIED',
  'MESSAGE_SENT',
  'TICKET_CREATED',
  'WORKFLOW_COMPLETED',
  'CASE_RESOLVED',
  'REFUND_STATE_CHECKED',
  'SIDE_EFFECT_CHECKED',
])

const MEMORY_EVENTS = new Set(['MEMORY_RETRIEVED', 'MEMORY_STORED'])

const ACTING_EVENTS = new Set([
  'DIAGNOSIS_COMPLETE',
  'PLAN_CREATED',
  'ACTION_STARTED',
  'ACTION_RETRIED',
  'ACTION_SCHEDULED',
  'REFUND_SCHEDULED',
  'RECOVERY_STARTED',
  'RECOVERY_DECIDED',
  'FAILURE_CLASSIFIED',
  'WORKFLOW_STARTED',
  'WORKFLOW_TICK',
  'MESSAGE_DRAFTED',
  'STATE_CHANGED',
  'VERIFICATION_PENDING',
  'CASE_RESUMED',
])

const DANGER_EVENTS = new Set([
  'ACTION_FAILED',
  'ESCALATION_CREATED',
  'CASE_ESCALATED',
  'HUMAN_REJECTED',
  'HUMAN_TAKEOVER',
  'AGENT_ERROR',
  'WORKFLOW_DISPATCH_FAILED',
])

const HUMAN_EVENTS = new Set(['HUMAN_APPROVED', 'HUMAN_REJECTED', 'HUMAN_TAKEOVER'])

/** Glyph comes from the event family, tone from its status. */
export function markFor(event: Pick<AgentEvent, 'type' | 'status' | 'result'>): TimelineMark {
  const { type, status } = event

  if (type === 'CASE_CREATED' || type === 'MESSAGE_RECEIVED') {
    return { glyph: 'dot', tone: 'neutral', spinning: false }
  }
  if (type === 'PROACTIVE_ALERT_CREATED') {
    return { glyph: 'radar', tone: 'caution', spinning: false }
  }
  if (type === 'POLICY_CHECKED') {
    const decision = (event.result as { decision?: string } | undefined)?.decision
    if (decision === 'DENY') return { glyph: 'warn', tone: 'danger', spinning: false }
    if (decision === 'REQUIRES_APPROVAL') return { glyph: 'warn', tone: 'caution', spinning: false }
    return { glyph: 'check', tone: 'verified', spinning: false }
  }
  if (HUMAN_EVENTS.has(type)) {
    return {
      glyph: 'human',
      tone: type === 'HUMAN_APPROVED' ? 'verified' : 'danger',
      spinning: false,
    }
  }
  if (DANGER_EVENTS.has(type)) return { glyph: 'warn', tone: 'danger', spinning: false }
  if (MEMORY_EVENTS.has(type)) return { glyph: 'brain', tone: 'memory', spinning: false }
  if (VERIFIED_EVENTS.has(type)) return { glyph: 'check', tone: 'verified', spinning: false }
  if (ACTING_EVENTS.has(type)) {
    return { glyph: 'cog', tone: 'acting', spinning: status === 'IN_PROGRESS' }
  }
  return { glyph: 'dot', tone: 'neutral', spinning: false }
}

export const toneClass: Record<Tone, string> = {
  neutral: 'text-ink-dim',
  verified: 'text-verified',
  acting: 'text-acting',
  memory: 'text-memory',
  danger: 'text-danger',
  caution: 'text-acting',
}

export function statusLabel(status: EventStatus): string {
  return status.replace('_', ' ').toLowerCase()
}

/** Seconds since the case was created, so time to first action is readable. */
export function offsetSeconds(event: AgentEvent, events: AgentEvent[]): number {
  const first = events[0]
  if (!first) return 0
  return (
    (new Date(event.timestamp).getTime() - new Date(first.timestamp).getTime()) / 1000
  )
}
