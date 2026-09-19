import type { Message } from '../types/api'

/**
 * Saarthi answers in the medium the merchant used.
 *
 * If the merchant typed, the reply is read. If they spoke, it is spoken back.
 * Keeping the rule here rather than inside the hook means it can be tested
 * without a DOM, an audio element or a fake timer.
 */
export function nextUtterance(
  messages: Pick<Message, 'id' | 'direction' | 'channel'>[],
  spoken: ReadonlySet<string>,
  { muted }: { muted: boolean },
): string | null {
  if (muted) return null

  const last = messages[messages.length - 1]
  if (!last || last.direction !== 'OUTBOUND') return null
  if (spoken.has(last.id)) return null

  // What the merchant last said, and how they said it.
  const asked = [...messages].reverse().find((m) => m.direction === 'INBOUND')
  if (!asked || asked.channel !== 'VOICE') return null

  return last.id
}

const MUTE_KEY = 'saarthi.voice.muted'
const MUTE_EVENT = 'saarthi:mute-changed'

export function readMuted(): boolean {
  try {
    return window.localStorage.getItem(MUTE_KEY) === 'true'
  } catch {
    // Private browsing and blocked site data both throw here.
    return false
  }
}

export function writeMuted(muted: boolean): void {
  try {
    window.localStorage.setItem(MUTE_KEY, String(muted))
  } catch {
    /* the preference simply will not persist */
  }
  window.dispatchEvent(new CustomEvent(MUTE_EVENT, { detail: muted }))
}

export { MUTE_EVENT }
