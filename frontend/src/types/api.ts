export type CaseStatus =
  | 'RECEIVED'
  | 'IDENTIFYING'
  | 'INVESTIGATING'
  | 'DIAGNOSING'
  | 'POLICY_CHECK'
  | 'PLANNING'
  | 'ACTING'
  | 'VERIFYING'
  | 'RECOVERING'
  | 'RESOLVED'
  | 'ESCALATED'

export type Risk = 'LOW' | 'MEDIUM' | 'HIGH'
export type PolicyDecisionType = 'ALLOW' | 'DENY' | 'REQUIRES_APPROVAL'
export type CaseOrigin = 'MERCHANT_CHAT' | 'MERCHANT_VOICE' | 'PROACTIVE' | 'DEMO'
export type Resolution = 'AUTONOMOUS' | 'HUMAN_APPROVED' | 'HUMAN_REJECTED' | 'HUMAN_TAKEOVER'

export interface Diagnosis {
  intent: string
  root_cause: string
  confidence: number
  risk: Risk
  requires_human: boolean
  summary: string
  evidence: string[]
  clamped_fields?: string[]
  suggested_actions?: string[]
  requested_amount?: string | null
}

export interface PolicySnapshot {
  action: string
  decision: PolicyDecisionType
  policy_ids: string[]
  reasons: string[]
}

export interface CurrentAction {
  type: string
  status: string
  attempt: number
  next_check_at?: string | null
}

export interface Case {
  id: string
  status: CaseStatus
  origin: CaseOrigin
  merchant_id: string
  transaction_id: string | null
  intent: string | null
  diagnosis: Diagnosis | null
  risk: Risk | null
  policy: PolicySnapshot | null
  current_action: CurrentAction | null
  requires_human: boolean
  human_required_by_policy: boolean
  owner: string
  wait_reason: string | null
  resolution: Resolution | null
  pending_escalation_id: string | null
  original_message: string
  created_at: string
  updated_at: string
  resolved_at: string | null
}

export type EventStatus = 'SUCCESS' | 'FAILED' | 'IN_PROGRESS' | 'INFO' | 'WARNING'

export interface AgentEvent {
  id: string
  case_id: string
  type: string
  status: EventStatus
  actor: string
  message: string
  result?: Record<string, unknown> | null
  metadata?: Record<string, unknown> | null
  sequence: number
  timestamp: string
}

export interface MemoryHit {
  kind: string
  doc_id: string
  case_id: string | null
  title: string
  summary: string
  diagnosis: string | null
  resolution: string | null
  resolution_time_seconds: number | null
  merchant_id: string | null
  similarity: number
  source: string
}

export interface MemoryContext {
  query: string
  similar_cases: MemoryHit[]
  knowledge: MemoryHit[]
  merchant_history: {
    merchant_id: string
    previous_case_count: number
    by_intent: Record<string, number>
    last_case_at: string | null
  } | null
  provider: string
  degraded: boolean
  latency_ms: number
}

export type PatternSeverity = 'LOW' | 'MEDIUM' | 'HIGH'

/** Counted from Postgres, never inferred by a model — and never current state. */
export interface MerchantPattern {
  merchant_id: string
  pattern_type: string
  event_count: number
  window_days: number
  threshold: number
  severity: PatternSeverity
  confidence: number
  summary: string
  recommended_attention: string
  first_seen: string
  last_seen: string
  related_cases: string[]
  related_transactions: string[]
  source: string
  authoritative: boolean
}

export interface MerchantProfile {
  merchant_id: string
  generated_at: string
  total_cases: number
  resolved_cases: number
  autonomous_resolutions: number
  needed_a_person: number
  by_intent: Record<string, number>
  median_resolution_seconds: number | null
  patterns: MerchantPattern[]
  headlines: string[]
}

export interface CaseContext {
  case_id: string
  merchant: {
    id: string
    name: string
    risk_level: Risk
    autonomous_refund_limit: string
    currency: string
  }
  transaction: {
    id: string
    amount: string
    currency: string
    payment_status: string
    customer_debited: boolean
    customer_reference: string
    description: string
    refunded_amount: string
  } | null
  payment_history: { kind: string; detail: Record<string, unknown>; occurred_at: string }[]
  settlement: {
    id: string
    status: string
    expected_at: string | null
    completed_at: string | null
    delay_reason: string | null
  } | null
  settlement_eta: { eta_seconds: number | null; overdue: boolean } | null
  disputes: {
    id: string
    type: string
    status: string
    description: string
    requested_amount: string | null
  }[]
  refunds: { id: string; amount: string; status: string; attempt_count: number }[]
  merchant_history: { previous_case_count: number; by_intent: Record<string, number> }
  notifications?: Reconciliation[]
  patterns: MerchantPattern[]
  policy: PolicySnapshot | null
  memory: MemoryContext | null
}

export interface Escalation {
  id: string
  case_id: string
  reason: string
  risk: Risk | null
  amount: string | null
  recommendation: string
  status: 'PENDING_HUMAN' | 'APPROVED' | 'REJECTED' | 'TAKEN_OVER'
  assigned_to: string | null
  human_decision: { decision: string; note?: string | null } | null
  pending_action: { tool: string; args: Record<string, unknown>; purpose: string } | null
  completed_actions: string[]
  context_snapshot: Record<string, any>
  policy: { decision: string; policy_ids: string[]; reasons: string[] } | null
  decided_by: string | null
  created_at: string
  resolved_at: string | null
  case_summary: string
  merchant_id: string | null
  transaction_id: string | null
}

export interface MetricValue {
  key: string
  label: string
  value: number | null
  unit: 'count' | 'ratio' | 'seconds' | 'hours'
  description: string
  numerator: number | null
  denominator: number | null
  simulated: boolean
}

export interface Metrics {
  generated_at: string
  metrics: MetricValue[]
  note: string
}

export interface Message {
  id: string
  direction: 'INBOUND' | 'OUTBOUND'
  channel: string
  sender: string
  content: string
  status: string
  created_at: string
}

export interface ProactiveAlert {
  id: string
  case_id: string
  merchant_id: string
  merchant_name: string
  transaction_id: string
  overdue_seconds: number
  created_at: string
}

export interface WorkflowRun {
  id: string
  workflow: string
  engine: string
  status: string
  attempts: number
  state: Record<string, unknown>
  next_check_at: string | null
  created_at: string
}

export interface Health {
  status: string
  llm: { provider: string; simulated: boolean; backend?: string | null }
  memory: { provider: string }
  workflows: { engine: string; reachable?: boolean }
  voice: { provider: string; model: string }
  tts: {
    provider: string
    model: string
    speaker: string
    language: string
    enabled: boolean
    characters_synthesised: number
  }
  database: string
  simulated: {
    llm: boolean
    memory: boolean
    workflows: boolean
    voice: boolean
    tts: boolean
  }
  all_real: boolean
}

export interface DemoStep {
  label: string
  detail: string
  control: string | null
}

export interface ScenarioCheckpoint {
  label: string
  event_type: string | null
  case_status: string | null
}

export interface Scenario {
  id: string
  name: string
  description: string
  capability: string
  merchant_id: string
  transaction_id: string | null
  /** Null for the proactive scenario: nobody says anything. */
  message: string | null
  trigger: 'MERCHANT_MESSAGE' | 'MONITOR'
  aliases: string[]
  initial_state: string[]
  expected_behaviour: string[]
  expected_outcome: string
  autonomy_boundary: string
  checkpoints: ScenarioCheckpoint[]
  demo_steps: DemoStep[]
}

export interface ScenarioStatus {
  scenario: string
  armed: boolean
  case_id: string | null
  case_status?: CaseStatus
  origin?: CaseOrigin
  resolution?: Resolution | null
  requires_human?: boolean
  human_required_by_policy?: boolean
  phase: 'NOT_STARTED' | 'RUNNING' | 'WAITING' | 'AWAITING_HUMAN' | 'RESOLVED'
  checkpoints: { label: string; reached: boolean }[]
  reached?: number
  total?: number
}

/** What a device told the merchant, already checked against the ledger. */
export interface Reconciliation {
  notification_id: string
  reference: string
  announced_amount: string | null
  announced_at: string
  outcome:
    | 'MATCHED_SUCCESS'
    | 'MATCHED_PENDING'
    | 'MATCHED_FAILED'
    | 'AMOUNT_MISMATCH'
    | 'NO_AUTHORITATIVE_RECORD'
  transaction_id: string | null
  ledger_amount: string | null
  payment_status: string | null
  explanation: string
  confirmed_by_ledger: boolean
  authoritative: false
}

export interface TranscribeResult {
  text: string
  language: string | null
  provider: string
  model: string
  latency_ms: number
  simulated: boolean
}
