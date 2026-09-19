import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../services/api'
import { MUTE_EVENT, nextUtterance, readMuted, writeMuted } from '../lib/speech'
import type { Message } from '../types/api'

export function useMuted(): [boolean, (muted: boolean) => void] {
  const [muted, setMuted] = useState(readMuted)

  useEffect(() => {
    const sync = () => setMuted(readMuted())
    window.addEventListener(MUTE_EVENT, sync)
    return () => window.removeEventListener(MUTE_EVENT, sync)
  }, [])

  return [muted, writeMuted]
}

/**
 * Playback of Saarthi's replies.
 *
 * One audio element for the whole panel, so a new reply interrupts the old one
 * rather than talking over it. The audio is fetched straight from its URL: a
 * sent message never changes, so the browser may cache it and there is no blob
 * lifecycle to leak.
 */
export function useSpeech(messages: Message[], { enabled }: { enabled: boolean }) {
  const [muted, setMuted] = useMuted()
  const [speakingId, setSpeakingId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const audioRef = useRef<HTMLAudioElement | null>(null)
  const spokenRef = useRef<Set<string>>(new Set())
  const primedRef = useRef(false)

  if (!audioRef.current && typeof Audio !== 'undefined') {
    audioRef.current = new Audio()
  }

  const stop = useCallback(() => {
    const audio = audioRef.current
    if (audio) {
      audio.pause()
      audio.removeAttribute('src')
    }
    setSpeakingId(null)
  }, [])

  const play = useCallback(
    (messageId: string) => {
      const audio = audioRef.current
      if (!audio || !enabled) return

      // Marked spoken *before* play, so React StrictMode's double-invoked
      // effect cannot fire two overlapping requests and abort the first.
      spokenRef.current.add(messageId)
      setError(null)
      setSpeakingId(messageId)

      audio.pause()
      audio.src = api.speechUrl(messageId)
      audio.onended = () => setSpeakingId((id) => (id === messageId ? null : id))
      audio.onerror = () => {
        setSpeakingId((id) => (id === messageId ? null : id))
        setError('Voice unavailable. The message is above.')
      }

      void audio.play().catch((reason: DOMException) => {
        setSpeakingId((id) => (id === messageId ? null : id))
        // A delayed autoplay with no recent user gesture; not an error, the
        // merchant just has to ask for it.
        if (reason?.name === 'NotAllowedError') return
        // StrictMode or a rapid second reply superseded this one.
        if (reason?.name === 'AbortError') return
        setError('Voice unavailable. The message is above.')
      })
    },
    [enabled],
  )

  // Everything already on screen counts as heard, so opening an old case is
  // silent and only replies that actually arrive get spoken.
  useEffect(() => {
    if (primedRef.current || messages.length === 0) return
    primedRef.current = true
    messages.forEach((m) => spokenRef.current.add(m.id))
  }, [messages])

  useEffect(() => {
    if (!enabled || !primedRef.current) return
    const candidate = nextUtterance(messages, spokenRef.current, { muted })
    if (candidate) play(candidate)
  }, [messages, muted, enabled, play])

  useEffect(() => () => stop(), [stop])

  return {
    speakingId,
    error,
    muted,
    setMuted,
    play,
    stop,
    toggle: (messageId: string) => (speakingId === messageId ? stop() : play(messageId)),
  }
}
