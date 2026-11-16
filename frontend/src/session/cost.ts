import type { Session, SessionStatus, SessionUsage, Stage } from '../api/client'
import { formatDuration } from '../lib/format.ts'
import { doneLive, stageSeconds, STAGE_LABELS, STAGE_ORDER, type RunState } from './state.ts'

export interface Cost {
  usd: number
  partial: boolean
}

const USD = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
})

function floorCents(usd: number): number {
  return Math.floor(Math.round(usd * 1e6) / 1e4) / 100
}

export function formatCost(cost: Cost): string {
  const usd = Math.max(0, cost.usd)
  if (cost.partial) return `≥ ${USD.format(floorCents(usd))}`
  if (usd > 0 && usd < 0.01) return '< $0.01'
  return USD.format(usd)
}

export function costLabel(cost: Cost): string {
  const usd = Math.max(0, cost.usd)
  if (cost.partial) return `at least ${USD.format(floorCents(usd))}, some calls unpriced`
  if (usd > 0 && usd < 0.01) return 'less than 1 cent'
  return USD.format(usd)
}

const NONE_YET = (status: SessionStatus): Cost | null =>
  status === 'done' ? { usd: 0, partial: false } : null

export function sessionCost(session: Session): Cost | null {
  if (session.cost_usd == null) return NONE_YET(session.status)
  return { usd: session.cost_usd, partial: session.cost_partial }
}

export function usageCost(usage: SessionUsage, status: SessionStatus): Cost | null {
  const t = usage.total
  return t.calls > 0 ? { usd: t.cost_usd, partial: t.unpriced_calls > 0 } : NONE_YET(status)
}

export interface StepCost {
  stage?: Stage
  label: string
  cost: Cost
}

export function stepCosts(usage: SessionUsage): StepCost[] {
  const known = STAGE_ORDER as readonly string[]
  return Object.entries(usage.by_stage)
    .filter(([, t]) => t.calls > 0)
    .map(([key, t]): StepCost => {
      const stage = known.includes(key) ? (key as Stage) : undefined
      return {
        stage,
        label: stage ? STAGE_LABELS[stage] : 'Other',
        cost: { usd: t.cost_usd, partial: t.unpriced_calls > 0 },
      }
    })
    .sort((a, b) => order(a.stage) - order(b.stage))
}

const order = (stage: Stage | undefined) => (stage ? STAGE_ORDER.indexOf(stage) : STAGE_ORDER.length)

export interface DetailRow {
  key: string
  label: string
  cost?: Cost
  time?: string
}

export function detailRows(run: RunState, costs: readonly StepCost[]): DetailRow[] {
  const costOf = new Map(costs.map((c) => [c.stage, c.cost]))
  const rows = STAGE_ORDER.flatMap((s): DetailRow[] => {
    const step = run.stages[s]
    const seconds = stageSeconds(step)
    const time = doneLive(step) ? 'while recording' : seconds !== undefined ? formatDuration(seconds) : undefined
    const cost = costOf.get(s)
    return time || cost ? [{ key: s, label: STAGE_LABELS[s], cost, time }] : []
  })
  const other = costOf.get(undefined)
  return other ? [...rows, { key: 'other', label: 'Other', cost: other }] : rows
}

export function usageRefreshKey(run: RunState, status: SessionStatus | undefined): string {
  return [run.status ?? status ?? '', ...STAGE_ORDER.map((s) => run.stages[s].finishedAt ?? '')].join('|')
}
