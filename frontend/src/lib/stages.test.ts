import { describe, expect, it } from 'vitest'
import { stageStates } from './stages'
import type { Case } from '../types/api'

const base = { id: 'CASE-1', status: 'ACTING' } as Case

describe('stage strip', () => {
  it('marks the current stage active and earlier stages done', () => {
    const states = stageStates(base, new Set(['RECEIVED', 'IDENTIFYING', 'INVESTIGATING']))
    expect(states.ACT).toBe('active')
    expect(states.PERCEIVE).toBe('done')
    expect(states.OUTCOME).toBe('pending')
  })

  it('hides recovery unless something actually went wrong', () => {
    expect(stageStates(base, new Set()).RECOVER).toBe('skipped')
    const recovered = stageStates(base, new Set(['RECOVERING']))
    expect(recovered.RECOVER).toBe('done')
  })

  it('shows an escalated outcome as needing a person, not as success', () => {
    const escalated = { ...base, status: 'ESCALATED' } as Case
    expect(stageStates(escalated, new Set()).OUTCOME).toBe('failed')
    const resolved = { ...base, status: 'RESOLVED' } as Case
    expect(stageStates(resolved, new Set()).OUTCOME).toBe('done')
  })
})
