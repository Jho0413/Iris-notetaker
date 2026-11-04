// Model cost as the UI shows it (docs/frontend.md §2: the rail's Cost slot and the library's
// Cost column). Pure functions, testable under plain Node (tests/cost.test.ts). Costs are shown
// per step with the plain step labels; model and provider names never reach the UI.
import type { Session, SessionStatus, SessionUsage, Stage } from '../api/client'
import {
  doneLive, formatDuration, stageSeconds, STAGE_LABELS, STAGE_ORDER, type RunState,
} from './state.ts'

export interface Cost {
  usd: number
  /** Some calls are unpriced (no price in the table), so `usd` is a lower bound. */
  partial: boolean
}

// One fixed format everywhere (not the browser's locale), so the rail and library always agree.
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2,
})

/** Whole cents, rounded down: a lower bound must never be rounded up past the true value. */
function floorCents(usd: number): number {
  // Rounding to micro-dollars first keeps float noise (0.29 * 100 = 28.999…) from losing a cent.
  return Math.floor(Math.round(usd * 1e6) / 1e4) / 100
}

/** "$0.89", "$1,234.50", "< $0.01" below a cent, "$0.00"; partial: "≥ $0.89" (rounded down). */
export function formatCost(cost: Cost): string {
  const usd = Math.max(0, cost.usd)
  if (cost.partial) return `≥ ${USD.format(floorCents(usd))}`
  if (usd > 0 && usd < 0.01) return '< $0.01'
  return USD.format(usd)
}

/** What a screen reader is told: words instead of "≥" and "<". */
export function costLabel(cost: Cost): string {
  const usd = Math.max(0, cost.usd)
  if (cost.partial) return `at least ${USD.format(floorCents(usd))}, some calls unpriced`
  if (usd > 0 && usd < 0.01) return 'less than 1 cent'
  return USD.format(usd)
}

// A session with no model calls shows nothing while it is unfinished, and "$0.00" once it is
// finished (docs/frontend.md §2, Library).
const NONE_YET = (status: SessionStatus): Cost | null =>
  status === 'done' ? { usd: 0, partial: false } : null

/** The library row's cost, from the totals the session list returns; null: nothing to show. */
export function sessionCost(session: Session): Cost | null {
  if (session.cost_usd == null) return NONE_YET(session.status)
  return { usd: session.cost_usd, partial: session.cost_partial }
}

/** The rail's total, from `GET /usage`; null: nothing to show. */
export function usageCost(usage: SessionUsage, status: SessionStatus): Cost | null {
  const t = usage.total
  return t.calls > 0 ? { usd: t.cost_usd, partial: t.unpriced_calls > 0 } : NONE_YET(status)
}

export interface StepCost {
  /** A pipeline stage, or undefined for calls made outside any stage. */
  stage?: Stage
  label: string
  cost: Cost
}

/** Cost per step with calls, in pipeline order (calls outside a stage last, as "Other"). */
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
  /** How long the step took in the latest run ("3 min 41 s", "while recording"). */
  time?: string
}

/**
 * The rail's Details rows: every step with a time (latest run, from events) or a cost (all runs),
 * in pipeline order with plain labels, then calls made outside any step as "Other".
 */
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

/**
 * Changes when the rail's cost should be fetched again: a step finished, or the session's status
 * changed (e.g. finished or failed). Other events (counts, figures) leave it unchanged, so cost
 * is not refetched on every event.
 */
export function usageRefreshKey(run: RunState, status: SessionStatus | undefined): string {
  return [run.status ?? status ?? '', ...STAGE_ORDER.map((s) => run.stages[s].finishedAt ?? '')].join('|')
}
