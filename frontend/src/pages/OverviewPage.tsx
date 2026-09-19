import { Link } from 'react-router-dom'
import { Radar } from 'lucide-react'
import { useAlerts, useCases, useMetrics } from '../hooks/queries'
import { clock, duration, humanise, metricDisplay } from '../lib/format'
import { Chip, Empty, MicroLabel, Mono, Panel, statusTone } from '../components/ui'
import { MerchantSupportBar } from '../components/chat/MerchantSupportBar'
import type { MetricValue } from '../types/api'

function MetricTile({ metric }: { metric: MetricValue }) {
  return (
    <div className="panel p-3.5" title={metric.description}>
      <div className="flex items-start justify-between gap-2">
        <div className="micro-label !mb-0">{metric.label}</div>
        {metric.simulated && <Chip tone="acting">simulated</Chip>}
      </div>
      <div className="mono mt-2 text-2xl font-semibold text-ink">
        {metricDisplay(metric.unit, metric.value)}
      </div>
      {metric.denominator !== null && metric.numerator !== null && (
        <Mono className="text-[10px] text-ink-faint">
          {metric.numerator} of {metric.denominator}
        </Mono>
      )}
    </div>
  )
}

export function OverviewPage() {
  const { data: metrics } = useMetrics()
  const { data: cases } = useCases()
  const { data: alerts } = useAlerts()

  return (
    <div className="space-y-5">
      <MerchantSupportBar />

      {alerts && alerts.length > 0 && (
        <div className="panel border-acting/40 bg-acting/5">
          {alerts.map((alert) => (
            <div key={alert.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3">
              <div className="flex items-center gap-2">
                <Radar size={15} className="text-acting" />
                <span className="mono text-[11px] font-semibold tracking-wider text-acting uppercase">
                  Proactive alert
                </span>
              </div>
              <span className="text-[13px] text-ink">
                Settlement delay detected for {alert.merchant_name} on{' '}
                <Mono>{alert.transaction_id}</Mono>, expected{' '}
                {duration(alert.overdue_seconds)} ago. Saarthi opened a case before the merchant
                reported anything.
              </span>
              <Link
                to={`/cases/${alert.case_id}`}
                className="mono ml-auto text-[11px] text-acting underline-offset-2 hover:underline"
              >
                open {alert.case_id}
              </Link>
            </div>
          ))}
        </div>
      )}

      <div>
        <MicroLabel>Operations</MicroLabel>
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
          {metrics?.metrics.map((m) => <MetricTile key={m.key} metric={m} />)}
        </div>
        {metrics && (
          <p className="mt-2 text-[11px] text-ink-faint">{metrics.note}</p>
        )}
      </div>

      <Panel title="Cases">
        {!cases || cases.length === 0 ? (
          <Empty>No cases yet. Open the demo console to run a scenario.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead>
                <tr className="border-b border-line">
                  {['Case', 'Merchant', 'Transaction', 'Diagnosis', 'Status', 'Outcome', 'Opened'].map(
                    (h) => (
                      <th key={h} className="micro-label !mb-0 px-2 py-2">
                        {h}
                      </th>
                    ),
                  )}
                </tr>
              </thead>
              <tbody>
                {cases.map((kase) => (
                  <tr key={kase.id} className="border-b border-line/60 last:border-0">
                    <td className="px-2 py-2">
                      <Link
                        to={`/cases/${kase.id}`}
                        className="mono text-[12px] text-ink underline-offset-2 hover:underline"
                      >
                        {kase.id}
                      </Link>
                      {kase.origin === 'PROACTIVE' && (
                        <Chip tone="acting">
                          <Radar size={9} /> proactive
                        </Chip>
                      )}
                    </td>
                    <td className="mono px-2 py-2 text-[12px] text-ink-dim">{kase.merchant_id}</td>
                    <td className="mono px-2 py-2 text-[12px] text-ink-dim">
                      {kase.transaction_id ?? '—'}
                    </td>
                    <td className="px-2 py-2 text-[12px] text-ink-dim">
                      {humanise(kase.diagnosis?.root_cause)}
                    </td>
                    <td className="px-2 py-2">
                      <Chip tone={statusTone(kase.status)}>{kase.status}</Chip>
                    </td>
                    <td className="px-2 py-2 text-[12px] text-ink-dim">
                      {kase.resolution ? humanise(kase.resolution) : '—'}
                    </td>
                    <td className="mono px-2 py-2 text-[11px] text-ink-faint">
                      {clock(kase.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  )
}
