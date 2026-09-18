import type { ReactNode } from 'react'

export function MicroLabel({ children }: { children: ReactNode }) {
  return <div className="micro-label mb-1.5">{children}</div>
}

export function Panel({
  title,
  action,
  children,
  className = '',
}: {
  title?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`panel ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div className="micro-label !mb-0">{title}</div>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

type ChipTone = 'neutral' | 'verified' | 'acting' | 'danger' | 'memory'

const CHIP: Record<ChipTone, string> = {
  neutral: 'border-line-bright text-ink-dim',
  verified: 'border-verified/40 text-verified bg-verified/10',
  acting: 'border-acting/40 text-acting bg-acting/10',
  danger: 'border-danger/40 text-danger bg-danger/10',
  memory: 'border-memory/40 text-memory bg-memory/10',
}

export function Chip({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode
  tone?: ChipTone
  title?: string
}) {
  return (
    <span
      title={title}
      className={`mono inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${CHIP[tone]}`}
    >
      {children}
    </span>
  )
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <MicroLabel>{label}</MicroLabel>
      <div className="text-[13px] leading-snug">{children}</div>
    </div>
  )
}

export function Mono({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <span className={`mono ${className}`}>{children}</span>
}

export function Button({
  children,
  onClick,
  tone = 'neutral',
  disabled,
  size = 'md',
  title,
}: {
  children: ReactNode
  onClick?: () => void
  tone?: 'neutral' | 'primary' | 'danger' | 'ghost'
  disabled?: boolean
  size?: 'sm' | 'md'
  title?: string
}) {
  const tones = {
    neutral: 'border-line-bright bg-panel-raised text-ink hover:border-ink-faint',
    primary: 'border-verified/50 bg-verified/15 text-verified hover:bg-verified/25',
    danger: 'border-danger/50 bg-danger/15 text-danger hover:bg-danger/25',
    ghost: 'border-transparent text-ink-dim hover:text-ink hover:border-line',
  }
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={`mono rounded-sm border font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        size === 'sm' ? 'px-2 py-1 text-[11px]' : 'px-3 py-1.5 text-xs'
      } ${tones[tone]}`}
    >
      {children}
    </button>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-xs text-ink-faint">{children}</div>
}

export function riskTone(risk: string | null | undefined): ChipTone {
  if (risk === 'HIGH') return 'danger'
  if (risk === 'MEDIUM') return 'acting'
  return 'verified'
}

export function decisionTone(decision: string | null | undefined): ChipTone {
  if (decision === 'ALLOW') return 'verified'
  if (decision === 'DENY') return 'danger'
  if (decision === 'REQUIRES_APPROVAL') return 'acting'
  return 'neutral'
}

export function statusTone(status: string): ChipTone {
  if (status === 'RESOLVED') return 'verified'
  if (status === 'ESCALATED') return 'danger'
  if (status === 'RECOVERING') return 'danger'
  if (status === 'VERIFYING' || status === 'ACTING') return 'acting'
  return 'neutral'
}
