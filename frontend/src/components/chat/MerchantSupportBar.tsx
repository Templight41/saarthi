import { useRef, useState } from 'react'
import { Loader2, Mic, Send, Square } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../services/api'
import { useRefreshAll } from '../../hooks/queries'
import { Button } from '../ui'

type VoiceState = 'idle' | 'listening' | 'transcribing'

/**
 * How a merchant would actually reach Saarthi, if this sat inside a payments
 * app: one box, type or speak, and the case opens.
 *
 * Both paths converge on the same `POST /api/cases`. Speaking only changes the
 * channel recorded on the message — which is what makes Saarthi answer aloud —
 * never how it reasons. The merchant is sent straight to the case so they
 * watch the work happen rather than waiting for a reply.
 */
export function MerchantSupportBar({ merchantId = 'M1001' }: { merchantId?: string }) {
  const [draft, setDraft] = useState('')
  const [voice, setVoice] = useState<VoiceState>('idle')
  const [note, setNote] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const spokeRef = useRef(false)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const navigate = useNavigate()
  const refresh = useRefreshAll()

  async function submit() {
    const message = draft.trim()
    if (!message || sending) return
    setSending(true)
    setNote(null)
    try {
      const created = await api.createCase({
        message,
        merchant_id: merchantId,
        channel: spokeRef.current ? 'VOICE' : 'CHAT',
      })
      setDraft('')
      spokeRef.current = false
      refresh()
      navigate(`/cases/${created.id}`)
    } catch (error) {
      setNote(error instanceof Error ? error.message : 'Could not open a case')
    } finally {
      setSending(false)
    }
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
          const result = await api.transcribe(new Blob(chunksRef.current, { type: mime }))
          setDraft(result.text)
          spokeRef.current = true
          setNote(`Heard via ${result.provider} · ${result.latency_ms}ms. Edit it or send.`)
        } catch {
          setNote('Could not transcribe that. Type the issue instead.')
        } finally {
          setVoice('idle')
        }
      }
      recorder.start()
      recorderRef.current = recorder
      setVoice('listening')
      setNote(null)
    } catch {
      setNote('Microphone unavailable. Type the issue instead.')
      setVoice('idle')
    }
  }

  return (
    <div className="panel p-4">
      <div className="mb-2.5 flex items-baseline gap-2">
        <span className="micro-label !mb-0">Talk to Saarthi</span>
        <span className="text-[11px] text-ink-faint">
          Describe the problem in your own words, in English or Hindi.
        </span>
      </div>

      <div className="flex gap-2">
        <input
          value={draft}
          onChange={(e) => {
            setDraft(e.target.value)
            spokeRef.current = false
          }}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Customer paid ₹2,500 but the transaction is still pending…"
          disabled={voice !== 'idle' || sending}
          aria-label="Describe your issue"
          className="flex-1 rounded-sm border border-line bg-ground px-3 py-2 text-[13px] text-ink outline-none placeholder:text-ink-faint focus:border-line-bright disabled:opacity-50"
        />

        {voice === 'listening' ? (
          <Button tone="danger" onClick={() => recorderRef.current?.stop()} title="Stop recording">
            <span className="flex items-center gap-1.5">
              <Square size={12} fill="currentColor" /> Stop
            </span>
          </Button>
        ) : (
          <Button
            onClick={startRecording}
            disabled={voice === 'transcribing' || sending}
            title="Speak to Saarthi"
          >
            {voice === 'transcribing' ? (
              <Loader2 size={12} className="spin-slow" />
            ) : (
              <Mic size={12} />
            )}
          </Button>
        )}

        <Button tone="primary" onClick={submit} disabled={!draft.trim() || sending}>
          <span className="flex items-center gap-1.5">
            {sending ? <Loader2 size={12} className="spin-slow" /> : <Send size={12} />} Send
          </span>
        </Button>
      </div>

      {(note || voice === 'listening') && (
        <div className="mono mt-2 text-[10px] text-ink-faint">
          {voice === 'listening' ? 'Listening…' : note}
        </div>
      )}
    </div>
  )
}
