import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../services/api'
import type { Case } from '../types/api'

/** Poll fast while the agent is working, slowly once the case is settled. */
function livePolling(kase: Case | undefined): number | false {
  if (!kase) return 1500
  const terminal = kase.status === 'RESOLVED'
  const awaitingHuman = kase.status === 'ESCALATED'
  if (terminal) return 6000
  if (awaitingHuman) return 3000
  return 1500
}

export function useHealth() {
  return useQuery({ queryKey: ['health'], queryFn: api.health, refetchInterval: 15000 })
}

export function useMetrics() {
  return useQuery({ queryKey: ['metrics'], queryFn: api.metrics, refetchInterval: 5000 })
}

export function useCases() {
  return useQuery({ queryKey: ['cases'], queryFn: () => api.listCases(), refetchInterval: 3000 })
}

export function useCase(id: string | undefined) {
  return useQuery({
    queryKey: ['case', id],
    queryFn: () => api.getCase(id!),
    enabled: Boolean(id),
    refetchInterval: (query) => livePolling(query.state.data),
  })
}

export function useTimeline(id: string | undefined, kase: Case | undefined) {
  return useQuery({
    queryKey: ['timeline', id],
    queryFn: () => api.timeline(id!),
    enabled: Boolean(id),
    refetchInterval: () => livePolling(kase),
  })
}

export function useCaseContext(id: string | undefined, kase: Case | undefined) {
  return useQuery({
    queryKey: ['context', id],
    queryFn: () => api.context(id!),
    enabled: Boolean(id),
    refetchInterval: () => livePolling(kase),
  })
}

export function useMessages(id: string | undefined, kase: Case | undefined) {
  return useQuery({
    queryKey: ['messages', id],
    queryFn: () => api.messages(id!),
    enabled: Boolean(id),
    refetchInterval: () => livePolling(kase),
  })
}

export function useWorkflows(id: string | undefined, kase: Case | undefined) {
  return useQuery({
    queryKey: ['workflows', id],
    queryFn: () => api.workflows(id!),
    enabled: Boolean(id),
    refetchInterval: () => livePolling(kase),
  })
}

export function useEscalations(status?: string) {
  return useQuery({
    queryKey: ['escalations', status],
    queryFn: () => api.escalations(status),
    refetchInterval: 3000,
  })
}

export function useAlerts() {
  return useQuery({ queryKey: ['alerts'], queryFn: api.alerts, refetchInterval: 3000 })
}

export function useScenarios() {
  return useQuery({ queryKey: ['scenarios'], queryFn: api.scenarios, staleTime: Infinity })
}

export function useRefreshAll() {
  const client = useQueryClient()
  return () => client.invalidateQueries()
}

export function useDecision() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision, note }: { id: string; decision: string; note?: string }) => {
      if (decision === 'approve') return api.approve(id, note)
      if (decision === 'reject') return api.reject(id, note)
      return api.takeover(id)
    },
    onSuccess: () => client.invalidateQueries(),
  })
}

export function useSendMessage(caseId: string | undefined) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ message, channel }: { message: string; channel?: string }) =>
      caseId
        ? api.sendMessage(caseId, message, channel)
        : api.createCase({ message, channel: channel ?? 'CHAT' }),
    onSuccess: () => client.invalidateQueries(),
  })
}
