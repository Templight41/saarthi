import { useRef, useState } from 'react'
import { Loader2, Mic, Send, Square } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../services/api'
import { useLanguages, useMerchants, useRefreshAll } from '../../hooks/queries'
import { Button, Mono } from '../ui'

const AUTO = 'auto'

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
  const { data: merchantInfo } = useMerchants()
  const { data: languageInfo } = useLanguages()
  const [merchant, setMerchant] = useState(merchantId)
  // `auto` means: say nothing, let the transcriber decide, and fall back to
  // the merchant's own default for anything typed.
  const [language, setLanguage] = useState(AUTO)
  const [detected, setDetected] = useState<string | null>(null)
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
        merchant_id: merchant,
        channel: spokeRef.current ? 'VOICE' : 'CHAT',
        // An explicit choice wins; otherwise whatever they were heard
        // speaking; otherwise nothing, and the merchant's default applies.
        language: language === AUTO ? detected : language,
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
          const result = await api.transcribe(
            new Blob(chunksRef.current, { type: mime }),
            undefined,
            language === AUTO ? undefined : language,
          )
          setDraft(result.text)
          spokeRef.current = true
          if (language === AUTO && result.language) {
            setDetected(result.language)
          }
          const heard = languageName(result.language)
          setNote(
            `Heard ${heard ? `${heard} ` : ''}via ${result.provider} · ${result.latency_ms}ms. ` +
              'Edit it or send.',
          )
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

  function languageName(code: string | null | undefined) {
    if (!code) return null
    return languageInfo?.languages.find((l) => l.code === code)?.name ?? code
  }

  const effective = language === AUTO ? detected : language
  const chosen = languageInfo?.languages.find((l) => l.code === effective)
  const select =
    'rounded-sm border border-line bg-ground px-2 py-1 text-[11px] text-ink outline-none focus:border-line-bright'

  return (
    <div className="panel p-4">
      <div className="mb-2.5 flex flex-wrap items-center gap-2">
        <span className="micro-label !mb-0">Talk to Saarthi</span>
        <span className="flex-1 text-[11px] text-ink-faint">
          Describe the problem in your own words, in any of these languages.
        </span>

        <select
          className={select}
          value={merchant}
          onChange={(e) => setMerchant(e.target.value)}
          aria-label="Merchant"
        >
          {(merchantInfo?.merchants ?? []).map((m) => (
            <option key={m.id} value={m.id}>
              {m.id} · {m.name}
            </option>
          ))}
        </select>

        <select
          className={select}
          value={language}
          onChange={(e) => {
            setLanguage(e.target.value)
            setDetected(null)
          }}
          aria-label="Language"
        >
          <option value={AUTO}>Auto-detect</option>
          {(languageInfo?.languages ?? []).map((l) => (
            <option key={l.code} value={l.code}>
              {l.endonym} · {l.name}
              {l.speakable ? '' : ' (text only)'}
            </option>
          ))}
        </select>
      </div>

      {chosen && (
        <div className="mb-2 flex items-center gap-2">
          <Mono className="text-[10px] text-ink-faint">
            {language === AUTO ? 'heard' : 'writing in'} {chosen.endonym}
          </Mono>
          {!chosen.speakable && (
            <Mono className="text-[10px] text-acting">
              written only — bulbul cannot speak {chosen.name}
            </Mono>
          )}
        </div>
      )}

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
