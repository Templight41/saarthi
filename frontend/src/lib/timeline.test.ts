import { describe, expect, it } from 'vitest'
import { markFor } from './timeline'

function event(type: string, status = 'SUCCESS', result?: Record<string, unknown>) {
  return { type, status: status as any, result }
}

describe('timeline marks', () => {
  it('shows verification and retrieval as verified', () => {
    for (const type of ['TRANSACTION_RETRIEVED', 'ACTION_VERIFIED', 'CASE_RESOLVED']) {
      const mark = markFor(event(type))
      expect(mark.glyph).toBe('check')
      expect(mark.tone).toBe('verified')
    }
  })

  it('gives memory its own tone so it is never mistaken for live state', () => {
    expect(markFor(event('MEMORY_RETRIEVED')).tone).toBe('memory')
    expect(markFor(event('MEMORY_STORED')).glyph).toBe('brain')
  })

  it('marks failures and escalations as dangerous', () => {
    expect(markFor(event('ACTION_FAILED', 'FAILED')).tone).toBe('danger')
    expect(markFor(event('ESCALATION_CREATED', 'WARNING')).glyph).toBe('warn')
  })

  it('tones a policy check by its decision, not its event type', () => {
    expect(markFor(event('POLICY_CHECKED', 'SUCCESS', { decision: 'ALLOW' })).tone).toBe('verified')
    expect(markFor(event('POLICY_CHECKED', 'WARNING', { decision: 'DENY' })).tone).toBe('danger')
    expect(markFor(event('POLICY_CHECKED', 'WARNING', { decision: 'REQUIRES_APPROVAL' })).tone).toBe(
      'caution',
    )
  })

  it('spins only while an action is actually in flight', () => {
    expect(markFor(event('ACTION_STARTED', 'IN_PROGRESS')).spinning).toBe(true)
    expect(markFor(event('ACTION_STARTED', 'SUCCESS')).spinning).toBe(false)
  })

  it('falls back to a neutral dot for an unknown event', () => {
    expect(markFor(event('SOMETHING_NEW')).glyph).toBe('dot')
  })
})
