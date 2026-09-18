const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
})

export function rupees(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const n = typeof value === 'string' ? Number(value) : value
  if (Number.isNaN(n)) return '—'
  return INR.format(n)
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const abs = Math.abs(Math.round(seconds))
  const h = Math.floor(abs / 3600)
  const m = Math.floor((abs % 3600) / 60)
  const s = abs % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

export function offsetLabel(seconds: number): string {
  if (seconds < 60) return `+${seconds.toFixed(1)}s`
  return `+${duration(seconds)}`
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-IN', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
}

export function humanise(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace(/_/g, ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase())
}

export function percent(value: number | null): string {
  if (value === null) return '—'
  return `${Math.round(value * 100)}%`
}

export function metricDisplay(unit: string, value: number | null): string {
  if (value === null) return '—'
  switch (unit) {
    case 'ratio':
      return percent(value)
    case 'seconds':
      return duration(value)
    case 'hours':
      return `${value.toFixed(1)}h`
    default:
      return String(value)
  }
}
