import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import {
  useCase,
  useCaseContext,
  useEscalations,
  useMessages,
  useTimeline,
  useWorkflows,
} from '../hooks/queries'
import { StageStrip } from '../components/case/StageStrip'
import {
  CurrentActionCard,
  DiagnosisCard,
  AnnouncementsPanel,
  MemoryPanel,
  PatternsPanel,
  PolicyCard,
  TransactionContextPanel,
  WorkflowRunsCard,
} from '../components/case/panels'
import { AgentTimeline } from '../components/timeline/AgentTimeline'
import { ApprovalCard } from '../components/escalation/ApprovalCard'
import { ChatPanel } from '../components/chat/ChatPanel'
import { Chip, Mono, Panel, statusTone } from '../components/ui'
import { humanise } from '../lib/format'

export function CaseWorkspacePage() {
  const { caseId } = useParams<{ caseId: string }>()
  const { data: kase } = useCase(caseId)
  const { data: events } = useTimeline(caseId, kase)
  const { data: ctx } = useCaseContext(caseId, kase)
  const { data: messages } = useMessages(caseId, kase)
  const { data: runs } = useWorkflows(caseId, kase)
  const { data: escalations } = useEscalations()

  if (!kase) return <div className="text-sm text-ink-dim">Loading case…</div>

  const live = kase.status !== 'RESOLVED'
  const escalation = escalations?.find((e) => e.case_id === kase.id)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link
          to="/"
          className="flex items-center gap-1.5 text-[12px] text-ink-dim hover:text-ink"
        >
          <ArrowLeft size={13} /> Overview
        </Link>
        <Mono className="text-[15px] font-semibold">{kase.id}</Mono>
        <Chip tone={statusTone(kase.status)}>{kase.status}</Chip>
        {kase.resolution && <Chip tone="verified">{humanise(kase.resolution)}</Chip>}
        {kase.owner === 'HUMAN' && <Chip tone="danger">owned by a person</Chip>}
        {kase.transaction_id && (
          <Mono className="text-[12px] text-ink-dim">{kase.transaction_id}</Mono>
        )}
      </div>

      <StageStrip kase={kase} events={events ?? []} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-4">
          <Panel title="Merchant said">
            <p className="text-[14px] leading-relaxed text-ink">{kase.original_message}</p>
          </Panel>

          {escalation && escalation.status === 'PENDING_HUMAN' && (
            <ApprovalCard escalation={escalation} />
          )}

          <DiagnosisCard kase={kase} />
          <PolicyCard kase={kase} />
          <CurrentActionCard kase={kase} />

          {caseId && (
            <ChatPanel
              caseId={caseId}
              messages={messages ?? []}
              status={kase.status}
              disabled={kase.owner === 'HUMAN'}
            />
          )}
        </div>

        <div className="space-y-4">
          {ctx && <TransactionContextPanel ctx={ctx} />}
          {ctx && <AnnouncementsPanel ctx={ctx} />}
          {ctx && <PatternsPanel ctx={ctx} />}
          {ctx && <MemoryPanel ctx={ctx} />}
          <WorkflowRunsCard runs={runs ?? []} />
        </div>
      </div>

      <Panel
        title="Agent activity"
        action={
          live && (
            <span className="mono flex items-center gap-1.5 text-[10px] text-acting">
              <span className="h-1.5 w-1.5 rounded-full bg-acting pulse" /> live
            </span>
          )
        }
      >
        <AgentTimeline events={events ?? []} live={live} />
      </Panel>
    </div>
  )
}
