import type {
  Case,
  CaseContext,
  Escalation,
  Health,
  Message,
  MerchantProfile,
  Metrics,
  ProactiveAlert,
  Scenario,
  AgentEvent,
  TranscribeResult,
  WorkflowRun,
} from '../types/api'

const BASE = import.meta.env.VITE_API_BASE ?? ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? body.error?.message ?? detail
    } catch {
      /* the body was not JSON; the status text will do */
    }
    throw new Error(`${response.status}: ${detail}`)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<Health>('/api/health'),
  metrics: () => request<Metrics>('/api/metrics'),

  listCases: (limit = 50) => request<Case[]>(`/api/cases?limit=${limit}`),
  getCase: (id: string) => request<Case>(`/api/cases/${id}`),
  createCase: (body: {
    message: string
    merchant_id?: string
    transaction_id?: string | null
    channel?: string
  }) => request<Case>('/api/cases', { method: 'POST', body: JSON.stringify(body) }),
  sendMessage: (id: string, message: string, channel = 'CHAT') =>
    request<Case>(`/api/cases/${id}/message`, {
      method: 'POST',
      body: JSON.stringify({ message, channel }),
    }),
  timeline: (id: string) =>
    request<{ events: AgentEvent[] }>(`/api/cases/${id}/timeline`).then((r) => r.events),
  context: (id: string) => request<CaseContext>(`/api/cases/${id}/context`),
  messages: (id: string) =>
    request<{ messages: Message[] }>(`/api/cases/${id}/messages`).then((r) => r.messages),
  workflows: (id: string) =>
    request<{ runs: WorkflowRun[] }>(`/api/cases/${id}/workflows`).then((r) => r.runs),

  merchantProfile: (id: string) => request<MerchantProfile>(`/api/merchants/${id}/profile`),

  escalations: (status?: string) =>
    request<{ escalations: Escalation[] }>(
      `/api/escalations${status ? `?status=${status}` : ''}`,
    ).then((r) => r.escalations),
  approve: (id: string, note?: string) =>
    request(`/api/escalations/${id}/approve`, {
      method: 'POST',
      body: JSON.stringify({ decided_by: 'ops@urbanthreads.in', note }),
    }),
  reject: (id: string, note?: string) =>
    request(`/api/escalations/${id}/reject`, {
      method: 'POST',
      body: JSON.stringify({ decided_by: 'ops@urbanthreads.in', note }),
    }),
  takeover: (id: string) =>
    request(`/api/escalations/${id}/takeover`, {
      method: 'POST',
      body: JSON.stringify({ assigned_to: 'ops@urbanthreads.in' }),
    }),

  alerts: () =>
    request<{ alerts: ProactiveAlert[] }>('/api/simulation/alerts').then((r) => r.alerts),
  scenarios: () =>
    request<{ scenarios: Scenario[] }>('/api/simulation/scenarios').then((r) => r.scenarios),
  reset: () => request('/api/simulation/reset', { method: 'POST' }),
  runScenario: (key: string) =>
    request<Scenario & { suggested_message: string }>(`/api/simulation/scenario/${key}`, {
      method: 'POST',
    }),
  armRefundFailure: (attempts = 1) =>
    request('/api/simulation/failure/refund', {
      method: 'POST',
      body: JSON.stringify({ attempts }),
    }),
  completeSettlement: (txn: string) =>
    request(`/api/simulation/settlement/${txn}/complete`, { method: 'POST' }),
  failSettlement: (txn: string) =>
    request(`/api/simulation/settlement/${txn}/fail`, { method: 'POST' }),
  runProactive: () => request('/api/simulation/proactive', { method: 'POST' }),

  /**
   * A sent message never changes, so its audio is a plain cacheable URL that an
   * audio element can take directly. There is deliberately no endpoint that
   * synthesises arbitrary text: Saarthi may only say what it has already sent.
   */
  speechUrl: (messageId: string) => `${BASE}/api/voice/messages/${messageId}/speech`,

  transcribe: async (blob: Blob, hint?: string): Promise<TranscribeResult> => {
    const form = new FormData()
    form.append('audio', blob, 'clip.webm')
    if (hint) form.append('hint', hint)
    const response = await fetch(`${BASE}/api/voice/transcribe`, { method: 'POST', body: form })
    if (!response.ok) throw new Error(`Transcription failed: ${response.status}`)
    return response.json()
  },
}
