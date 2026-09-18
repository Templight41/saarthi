import { useRef, useState } from 'react'
import { Loader2, Mic, Send, Square } from 'lucide-react'
import type { Message } from '../../types/api'
import { api } from '../../services/api'
import { useSendMessage } from '../../hooks/queries'
import { clock } from '../../lib/format'
import { Button, Empty, Mono, Panel } from '../ui'

type VoiceState = 'idle' | 'listening' | 'transcribing'

export function ChatPanel({
  caseId,
  messages,
  disabled,
}: {
  caseId: string
  messages: Message[]
  disabled?: boolean
}) {
  const [draft, setDraft] = useState('')
  const [voice, setVoice] = useState<VoiceState>('idle')
  const [voiceNote, setVoiceNote] = useState<string | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const send = useSendMessage(caseId)

  function submit(text: string, channel = 'CHAT') {
    const trimmed = text.trim()
    if (!trimmed) return
    send.mutate({ message: trimmed, channel })
    setDraft('')
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

  return (
    <Panel title="Merchant conversation">
      <div className="mb-3 max-h-72 space-y-2.5 overflow-y-auto pr-1">
        {messages.length === 0 ? (
          <Empty>No messages yet.</Empty>
        ) : (
          messages.map((m) => {
            const inbound = m.direction === 'INBOUND'
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
                </div>
                <p
                  className={`mt-0.5 rounded-sm border px-2.5 py-1.5 text-[13px] leading-relaxed ${
                    inbound
                      ? 'border-line bg-panel-raised text-ink'
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

      {voiceNote && <div className="mono mb-2 text-[10px] text-ink-faint">{voiceNote}</div>}

      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit(draft)}
          placeholder={disabled ? 'This case is closed' : 'Message Saarthi…'}
          disabled={disabled || voice !== 'idle'}
          className="flex-1 rounded-sm border border-line bg-ground px-2.5 py-1.5 text-[13px] text-ink outline-none placeholder:text-ink-faint focus:border-line-bright disabled:opacity-50"
        />

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
