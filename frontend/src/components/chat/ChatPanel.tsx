import { useRef, useState } from 'react'
import { Loader2, Mic, Send, Square, Volume2, VolumeX } from 'lucide-react'
import type { CaseStatus, Message } from '../../types/api'
import { api } from '../../services/api'
import { useHealth, useSendMessage } from '../../hooks/queries'
import { useSpeech } from '../../hooks/useSpeech'
import { clock } from '../../lib/format'
import { Button, Empty, Mono, Panel } from '../ui'

type VoiceState = 'idle' | 'listening' | 'transcribing'

/** Saarthi is mid-case: the merchant is waiting on it, not the other way round. */
const WORKING: CaseStatus[] = [
  'IDENTIFYING',
  'INVESTIGATING',
  'DIAGNOSING',
  'POLICY_CHECK',
  'PLANNING',
  'ACTING',
  'VERIFYING',
  'RECOVERING',
]

export function ChatPanel({
  caseId,
  messages,
  status,
  disabled,
}: {
  caseId: string
  messages: Message[]
  status?: CaseStatus
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  const [voice, setVoice] = useState<VoiceState>('idle')
  const [voiceNote, setVoiceNote] = useState<string | null>(null)
  // Whether the text in the box arrived by microphone. It decides the channel
  // the message is sent on, which in turn is what makes Saarthi answer aloud.
  const [spokeLast, setSpokeLast] = useState(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const send = useSendMessage(caseId)

  const health = useHealth()
  const canSpeak = health.data?.tts.enabled ?? true
  const speech = useSpeech(messages, { enabled: canSpeak })

  const working = Boolean(status && WORKING.includes(status))

  function submit(text: string) {
    const trimmed = text.trim()
    if (!trimmed) return
    send.mutate({ message: trimmed, channel: spokeLast ? 'VOICE' : 'CHAT' })
    setDraft('')
    setSpokeLast(false)
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/mp4'
      const recorder = new MediaRecorder(stream, { mimeType: mime })
      chunksRef.current = []
      recorder.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data)
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setVoice('transcribing')
        try {
          const blob = new Blob(chunksRef.current, { type: mime })
          const result = await api.transcribe(blob)
          // The transcript goes through the ordinary message path, so the
          // agent cannot tell this began as speech.
          setDraft(result.text)
          setSpokeLast(true)
          setVoiceNote(
            `${result.provider}${result.simulated ? ' (scripted)' : ''} · ${result.latency_ms}ms`,
          )
        } catch {
          setVoiceNote('Transcription failed. Type the message instead.')
        } finally {
          setVoice('idle')
        }
      }
      recorder.start()
      recorderRef.current = recorder
      setVoice('listening')
      setVoiceNote(null)
    } catch {
      setVoiceNote('Microphone unavailable. Type the message instead.')
      setVoice('idle')
    }
  }

  function stopRecording() {
    recorderRef.current?.stop()
    recorderRef.current = null
  }

  const note = speech.error ?? voiceNote

  return (
    <Panel
      title="Merchant conversation"
      action={
        voice === 'listening' ? (
          <span className="mono flex items-center gap-1.5 text-[10px] text-danger">
            <span className="h-1.5 w-1.5 rounded-full bg-danger pulse" /> listening
          </span>
        ) : voice === 'transcribing' ? (
          <span className="mono flex items-center gap-1.5 text-[10px] text-acting">
            <Loader2 size={10} className="spin-slow" /> transcribing
          </span>
        ) : speech.speakingId ? (
          <span className="mono flex items-center gap-1.5 text-[10px] text-verified">
            <Volume2 size={10} /> speaking
          </span>
        ) : working ? (
          <span className="mono flex items-center gap-1.5 text-[10px] text-acting">
            <span className="h-1.5 w-1.5 rounded-full bg-acting pulse" /> Saarthi is working
          </span>
        ) : null
      }
    >
      <div className="mb-3 max-h-72 space-y-2.5 overflow-y-auto pr-1">
        {messages.length === 0 ? (
          <Empty>No messages yet.</Empty>
        ) : (
          messages.map((m) => {
            const inbound = m.direction === 'INBOUND'
            const speaking = speech.speakingId === m.id
            return (
              <div key={m.id} className={inbound ? '' : 'pl-5'}>
                <div className="flex items-baseline gap-2">
                  <span
                    className={`mono text-[10px] font-semibold tracking-wider uppercase ${
                      inbound ? 'text-ink-dim' : 'text-verified'
                    }`}
                  >
                    {m.sender}
                  </span>
                  {m.channel === 'VOICE' && (
                    <Mono className="text-[9px] text-ink-faint">voice</Mono>
                  )}
                  <Mono className="text-[10px] text-ink-faint">{clock(m.created_at)}</Mono>
                  {!inbound && canSpeak && (
                    <button
                      type="button"
                      onClick={() => speech.toggle(m.id)}
                      title={speaking ? 'Stop' : 'Play this message'}
                      aria-label={speaking ? 'Stop speaking' : 'Play this message'}
                      className={`ml-auto rounded-sm p-0.5 transition-colors ${
                        speaking ? 'text-verified' : 'text-ink-faint hover:text-ink-dim'
                      }`}
                    >
                      {speaking ? <Square size={10} fill="currentColor" /> : <Volume2 size={11} />}
                    </button>
                  )}
                </div>
                <p
                  className={`mt-0.5 rounded-sm border px-2.5 py-1.5 text-[13px] leading-relaxed ${
                    inbound
                      ? 'border-line bg-panel-raised text-ink'
                      : speaking
                        ? 'border-verified/50 bg-verified/10 text-ink'
                        : 'border-verified/25 bg-verified/5 text-ink'
                  }`}
                >
                  {m.content}
                </p>
              </div>
            )
          })
        )}
      </div>

      {note && <div className="mono mb-2 text-[10px] text-ink-faint">{note}</div>}

      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            // Edited text is typed text, whatever produced the first draft.
            setSpokeLast(false)
          }}
          onKeyDown={(e) => e.key === 'Enter' && submit(draft)}
          placeholder={disabled ? 'This case is closed' : 'Message Saarthi…'}
          disabled={disabled || voice !== 'idle'}
          className="flex-1 rounded-sm border border-line bg-ground px-2.5 py-1.5 text-[13px] text-ink outline-none placeholder:text-ink-faint focus:border-line-bright disabled:opacity-50"
        />

        {canSpeak && (
          <Button
            onClick={() => speech.setMuted(!speech.muted)}
            tone="ghost"
            title={speech.muted ? 'Saarthi is muted' : 'Mute Saarthi'}
          >
            {speech.muted ? <VolumeX size={12} /> : <Volume2 size={12} />}
          </Button>
        )}

        {voice === 'listening' ? (
          <Button tone="danger" onClick={stopRecording} title="Stop recording">
            <span className="flex items-center gap-1.5">
              <Square size={12} fill="currentColor" /> Stop
            </span>
          </Button>
        ) : (
          <Button
            onClick={startRecording}
            disabled={disabled || voice === 'transcribing'}
            title="Speak to Saarthi"
          >
            {voice === 'transcribing' ? (
              <span className="flex items-center gap-1.5">
                <Loader2 size={12} className="spin-slow" /> Transcribing
              </span>
            ) : (
              <span className="flex items-center gap-1.5">
                <Mic size={12} /> Speak
              </span>
            )}
          </Button>
        )}

        <Button
          tone="primary"
          onClick={() => submit(draft)}
          disabled={disabled || !draft.trim() || send.isPending}
        >
          <Send size={12} />
        </Button>
      </div>
    </Panel>
  )
}
