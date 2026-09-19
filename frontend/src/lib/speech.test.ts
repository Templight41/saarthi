import { describe, expect, it } from 'vitest'
import { nextUtterance } from './speech'

type Row = { id: string; direction: 'INBOUND' | 'OUTBOUND'; channel: string }

const said = (id: string, channel = 'CHAT'): Row => ({ id, direction: 'INBOUND', channel })
const replied = (id: string): Row => ({ id, direction: 'OUTBOUND', channel: 'CHAT' })
const none = new Set<string>()

describe('nextUtterance', () => {
  it('speaks the reply when the merchant spoke', () => {
    expect(nextUtterance([said('m1', 'VOICE'), replied('s1')], none, { muted: false })).toBe('s1')
  })

  it('stays quiet when the merchant typed', () => {
    expect(nextUtterance([said('m1'), replied('s1')], none, { muted: false })).toBeNull()
  })

  it('never speaks the same message twice', () => {
    const messages = [said('m1', 'VOICE'), replied('s1')]
    expect(nextUtterance(messages, new Set(['s1']), { muted: false })).toBeNull()
  })

  it('speaks each new reply in a spoken conversation', () => {
    const messages = [said('m1', 'VOICE'), replied('s1'), replied('s2')]
    expect(nextUtterance(messages, new Set(['s1']), { muted: false })).toBe('s2')
  })

  it('never speaks the merchant back to themselves', () => {
    expect(nextUtterance([replied('s1'), said('m1', 'VOICE')], none, { muted: false })).toBeNull()
  })

  it('falls silent once the merchant switches to typing', () => {
    const messages = [said('m1', 'VOICE'), replied('s1'), said('m2'), replied('s2')]
    expect(nextUtterance(messages, new Set(['s1']), { muted: false })).toBeNull()
  })

  it('obeys the mute', () => {
    expect(nextUtterance([said('m1', 'VOICE'), replied('s1')], none, { muted: true })).toBeNull()
  })

  it('has nothing to say about an empty conversation', () => {
    expect(nextUtterance([], none, { muted: false })).toBeNull()
  })
})
