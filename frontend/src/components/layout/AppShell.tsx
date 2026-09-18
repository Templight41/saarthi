import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import { LayoutGrid, ShieldAlert, SlidersHorizontal, X } from 'lucide-react'
import { useEscalations, useHealth } from '../../hooks/queries'
import { DemoConsole } from '../demo/DemoConsole'
import { Chip, Mono } from '../ui'

function ProviderBadges() {
  const { data } = useHealth()
  if (!data) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Chip tone={data.llm.simulated ? 'neutral' : 'verified'} title="Language model in use">
        llm {data.llm.provider}
      </Chip>
      <Chip
        tone={data.memory.provider === 'local_index' ? 'neutral' : 'memory'}
        title="Semantic memory backend"
      >
        memory {data.memory.provider.replace('_', ' ')}
      </Chip>
      <Chip tone={data.workflows.engine === 'n8n' ? 'memory' : 'neutral'} title="Workflow engine">
        wf {data.workflows.engine}
      </Chip>
      <Chip tone="neutral" title="Database">
        db {data.database}
      </Chip>
    </div>
  )
}

export function AppShell() {
  const [demoOpen, setDemoOpen] = useState(false)
  const { data: escalations } = useEscalations('PENDING_HUMAN')
  const pending = escalations?.length ?? 0

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-2 rounded-sm px-2.5 py-1.5 text-[13px] transition-colors ${
      isActive ? 'bg-panel-raised text-ink' : 'text-ink-dim hover:text-ink'
    }`

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 flex flex-wrap items-center gap-x-5 gap-y-2 border-b border-line bg-ground/95 px-5 py-2.5 backdrop-blur">
        <div className="flex items-baseline gap-2">
          <span className="text-[15px] font-bold tracking-tight">Saarthi</span>
          <span className="mono text-[10px] tracking-wider text-ink-faint uppercase">
            merchant operations
          </span>
        </div>

        <nav className="flex items-center gap-1">
          <NavLink to="/" end className={navClass}>
            <LayoutGrid size={13} /> Overview
          </NavLink>
          <NavLink to="/escalations" className={navClass}>
            <ShieldAlert size={13} /> Escalations
            {pending > 0 && (
              <span className="mono rounded-full bg-danger px-1.5 text-[10px] font-semibold text-ground">
                {pending}
              </span>
            )}
          </NavLink>
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <ProviderBadges />
          <button
            type="button"
            onClick={() => setDemoOpen(true)}
            className="mono flex items-center gap-1.5 rounded-sm border border-line-bright bg-panel-raised px-2.5 py-1.5 text-[11px] text-ink transition-colors hover:border-ink-faint"
          >
            <SlidersHorizontal size={12} /> Demo
          </button>
        </div>
      </header>

      <main className="flex-1 px-5 py-5">
        <Outlet />
      </main>

      {demoOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-black/60"
            onClick={() => setDemoOpen(false)}
            aria-hidden
          />
          <aside className="fixed top-0 right-0 z-50 flex h-full w-[380px] max-w-full flex-col border-l border-line bg-panel">
            <header className="flex items-center justify-between border-b border-line px-4 py-3">
              <div>
                <div className="text-[14px] font-semibold">Demo console</div>
                <Mono className="text-[10px] text-ink-faint">deterministic and repeatable</Mono>
              </div>
              <button
                type="button"
                onClick={() => setDemoOpen(false)}
                className="text-ink-dim hover:text-ink"
              >
                <X size={16} />
              </button>
            </header>
            <div className="flex-1 overflow-y-auto p-4">
              <DemoConsole onDone={() => setDemoOpen(false)} />
            </div>
          </aside>
        </>
      )}
    </div>
  )
}
