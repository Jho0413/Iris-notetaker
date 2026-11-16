import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent, Session, SessionUsage } from '../src/api/client.ts'
import {
  costLabel, detailRows, formatCost, sessionCost, stepCosts, usageCost, usageRefreshKey,
} from '../src/session/cost.ts'
import { applyEvent, emptyRun, reduceEvents, type RunState } from '../src/session/state.ts'
import { ev, session as base } from './fixtures.ts'

const exact = (usd: number) => formatCost({ usd, partial: false })
const partial = (usd: number) => formatCost({ usd, partial: true })

test('formatCost: two decimals from a cent, "< $0.01" below, thousands grouped', () => {
  assert.equal(exact(0), '$0.00')
  assert.equal(exact(0.0001), '< $0.01')
  assert.equal(exact(0.00999), '< $0.01')
  assert.equal(exact(0.01), '$0.01')
  assert.equal(exact(0.014), '$0.01')
  assert.equal(exact(0.889), '$0.89')
  assert.equal(exact(0.995), '$1.00')
  assert.equal(exact(1234.5), '$1,234.50')
  assert.equal(exact(1_000_000), '$1,000,000.00')
  assert.equal(exact(-1), '$0.00')
})

test('formatCost: a partial total is a lower bound, rounded down and marked ≥', () => {
  assert.equal(partial(0.894), '≥ $0.89')
  assert.equal(partial(0.899), '≥ $0.89')
  assert.equal(partial(0.29), '≥ $0.29')
  assert.equal(partial(0), '≥ $0.00')
  assert.equal(partial(0.004), '≥ $0.00')
  assert.equal(partial(1234.567), '≥ $1,234.56')
})

test('costLabel: screen readers hear words, not symbols', () => {
  assert.equal(costLabel({ usd: 0.89, partial: false }), '$0.89')
  assert.equal(costLabel({ usd: 0.004, partial: false }), 'less than 1 cent')
  assert.equal(costLabel({ usd: 0.89, partial: true }), 'at least $0.89, some calls unpriced')
  assert.equal(costLabel({ usd: 0, partial: false }), '$0.00')
})

const session = (patch: Partial<Session>): Session => ({ ...base, cost_usd: null, cost_partial: false, ...patch })

test('library row: cost per state', () => {
  const shown = (patch: Partial<Session>) => {
    const cost = sessionCost(session(patch))
    return cost && formatCost(cost)
  }
  assert.equal(shown({ status: 'created' }), null)
  assert.equal(shown({ status: 'recording' }), null)
  assert.equal(shown({ status: 'processing' }), null)
  assert.equal(shown({ status: 'failed' }), null)
  assert.equal(shown({ status: 'done' }), '$0.00')
  assert.equal(shown({ status: 'processing', cost_usd: 0.3 }), '$0.30')
  assert.equal(shown({ status: 'recording', cost_usd: 0.004 }), '< $0.01')
  assert.equal(shown({ status: 'done', cost_usd: 0.894 }), '$0.89')
  assert.equal(shown({ status: 'failed', cost_usd: 0.12, cost_partial: true }), '≥ $0.12')
  assert.equal(shown({ status: 'done', cost_usd: 0, cost_partial: true }), '≥ $0.00')
})

const totals = (cost_usd: number, calls = 1, unpriced_calls = 0) => ({
  calls, unpriced_calls, failed_calls: 0, cost_usd, latency_s: 0, input_tokens: 0,
  cached_input_tokens: 0, input_image_tokens: 0, input_audio_tokens: 0, output_tokens: 0,
  reasoning_tokens: 0, output_image_tokens: 0, images: 0, audio_seconds: 0,
})

const usage = (by_stage: SessionUsage['by_stage']): SessionUsage => {
  const all = Object.values(by_stage)
  return {
    prices_checked: '2026-11-01',
    records: [],
    total: totals(
      all.reduce((sum, t) => sum + t.cost_usd, 0),
      all.reduce((sum, t) => sum + t.calls, 0),
      all.reduce((sum, t) => sum + t.unpriced_calls, 0),
    ),
    by_stage,
    by_model: { 'gpt-5.5': totals(9) },
    by_purpose: { figure_check: totals(9) },
  }
}

test('rail total: per state, partial when any call is unpriced', () => {
  const none = usage({})
  assert.equal(usageCost(none, 'processing'), null)
  assert.equal(usageCost(none, 'failed'), null)
  assert.deepEqual(usageCost(none, 'done'), { usd: 0, partial: false })
  const some = usage({ transcribe: totals(0.01), render: totals(0.74, 3, 1) })
  assert.deepEqual(usageCost(some, 'processing'), { usd: 0.75, partial: true })
  assert.equal(formatCost(usageCost(some, 'done')!), '≥ $0.75')
})

test('step costs: plain labels in pipeline order, no model names, "Other" last', () => {
  const steps = stepCosts(
    usage({ none: totals(0.02), render: totals(0.74, 2, 1), transcribe: totals(0.01), write: totals(0, 0) }),
  )
  assert.deepEqual(
    steps.map((s) => [s.label, formatCost(s.cost)]),
    [['Transcribing', '$0.01'], ['Redrawing figures', '≥ $0.74'], ['Other', '$0.02']],
  )
  assert.ok(!JSON.stringify(steps).includes('gpt'))
})

test('details rows: a step with a time, a cost or both; times from this run only', () => {
  const run = reduceEvents([
    ev('status', { status: 'processing' }, 0),
    ev('stage_started', { stage: 'transcribe' }, 1),
    ev('stage_finished', { stage: 'transcribe' }, 5),
    ev('stage_started', { stage: 'render' }, 10),
    ev('stage_finished', { stage: 'render' }, 231),
    ev('stage_started', { stage: 'assemble' }, 232),
  ])
  const rows = detailRows(run, stepCosts(usage({ render: totals(0.74), select: totals(0.2) })))
  assert.deepEqual(
    rows.map((r) => [r.label, r.cost && formatCost(r.cost), r.time]),
    [
      ['Transcribing', undefined, '4.0 s'],
      ['Reading the screen', '$0.20', undefined],
      ['Redrawing figures', '$0.74', '3 min 41 s'],
    ],
  )
  assert.deepEqual(detailRows(emptyRun(), []), [])
})

test('usage is refetched when a step or the session finishes, not on every event', () => {
  let run: RunState = applyEvent(emptyRun(), ev('status', { status: 'processing' }))
  const step = (event: IrisEvent) => {
    const before = usageRefreshKey(run, 'processing')
    run = applyEvent(run, event)
    return usageRefreshKey(run, 'processing') !== before
  }
  assert.equal(step(ev('stage_started', { stage: 'transcribe' })), false)
  assert.equal(step(ev('transcript_chunk', { chunk: 0, segments: [] })), false)
  assert.equal(step(ev('stage_finished', { stage: 'transcribe' })), true)
  assert.equal(step(ev('transcript_chunk', { chunk: 0, segments: [] })), false)
  assert.equal(step(ev('visual_selected', { id: 'v1', caption: 'A' })), false)
  assert.equal(step(ev('status', { status: 'done' })), true)
  assert.notEqual(usageRefreshKey(emptyRun(), 'processing'), usageRefreshKey(emptyRun(), 'done'))
})
