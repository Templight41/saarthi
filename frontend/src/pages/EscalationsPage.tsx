import { Link } from 'react-router-dom'
import { useEscalations } from '../hooks/queries'
import { ApprovalCard } from '../components/escalation/ApprovalCard'
import { Chip, Empty, MicroLabel, Mono, Panel } from '../components/ui'
import { clock, humanise, rupees } from '../lib/format'

export function EscalationsPage() {
  const { data: escalations } = useEscalations()
  const pending = escalations?.filter((e) => e.status === 'PENDING_HUMAN') ?? []
  const decided = escalations?.filter((e) => e.status !== 'PENDING_HUMAN') ?? []

  return (
    <div className="space-y-5">
      <div>
        <MicroLabel>Awaiting a decision</MicroLabel>
        {pending.length === 0 ? (
          <Panel>
            <Empty>Nothing is waiting on a person.</Empty>
          </Panel>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            {pending.map((e) => (
              <ApprovalCard key={e.id} escalation={e} />
            ))}
          </div>
        )}
      </div>

      {decided.length > 0 && (
        <Panel title="Decided">
          <ul className="space-y-2">
            {decided.map((e) => (
              <li key={e.id} className="flex flex-wrap items-center gap-2 text-[12px]">
                <Link
                  to={`/cases/${e.case_id}`}
                  className="mono text-ink underline-offset-2 hover:underline"
                >
                  {e.case_id}
                </Link>
                <Chip>{humanise(e.reason)}</Chip>
                {e.amount && <Mono className="text-ink-dim">{rupees(e.amount)}</Mono>}
                <Chip tone={e.status === 'APPROVED' ? 'verified' : 'danger'}>{e.status}</Chip>
                {e.decided_by && <Mono className="text-[11px] text-ink-faint">{e.decided_by}</Mono>}
                <Mono className="ml-auto text-[10px] text-ink-faint">{clock(e.created_at)}</Mono>
              </li>
            ))}
          </ul>
        </Panel>
      )}
    </div>
  )
}
