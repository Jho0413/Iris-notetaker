import type { Session } from '../api/client'

export function formatWhen(iso: string, now = new Date()): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const days = Math.round((day(now) - day(d)) / 86_400_000)
  if (days === 0) return `Today ${time}`
  if (days === 1) return `Yesterday ${time}`
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
  }
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return `${v >= 10 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

export function formatDuration(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)} s`
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  return `${m} min ${String(Math.round(seconds % 60)).padStart(2, '0')} s`
}

export const SOURCE_LABEL: Record<string, string> = { upload: 'Upload', browser_live: 'Live' }

export function shownTitle(title: string): string {
  return title || 'Untitled session'
}

export function sessionMetaLine(session: Session, sessionType?: string, length?: number): string[] {
  return [
    sessionType ? sessionType.charAt(0).toUpperCase() + sessionType.slice(1) : '',
    SOURCE_LABEL[session.source] ?? session.source,
    length !== undefined ? formatClock(length) : '',
    formatWhen(session.created_at),
  ].filter((part) => part !== '')
}

export function sessionLength(session: Session, segs: readonly { end: number }[]): number | undefined {
  if (session.duration_s) return session.duration_s
  const end = segs.reduce((max, s) => Math.max(max, s.end), 0)
  return end > 0 ? end : undefined
}
