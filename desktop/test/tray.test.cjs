const assert = require('node:assert/strict')
const { test } = require('node:test')
const { IDLE, RELAY_START, parseReport, trayView, clock, hideOnClose, relay } = require('../src/tray.cjs')

const T0 = 1_800_000_000_000
const actions = (view) => view.items.filter((i) => i.id).map((i) => i.id)
const labels = (view) => view.items.filter((i) => i.label).map((i) => i.label)

test('the Live page’s phases map onto the tray’s', () => {
  assert.deepEqual(parseReport({ phase: 'idle' }), { phase: 'idle', startedAt: 0 })
  assert.deepEqual(parseReport({ phase: 'requesting' }), { phase: 'starting', startedAt: 0 })
  assert.deepEqual(parseReport({ phase: 'recording', startedAt: T0 }), { phase: 'recording', startedAt: T0 })
  assert.deepEqual(parseReport({ phase: 'stopping', startedAt: T0 }), { phase: 'stopping', startedAt: T0 })
  assert.deepEqual(parseReport({ phase: 'finishing', startedAt: T0 }), { phase: 'stopping', startedAt: T0 })
  assert.deepEqual(parseReport({ phase: 'unfinished', startedAt: T0 }), { phase: 'unfinished', startedAt: 0 })
})

test('a malformed report is refused, not guessed at', () => {
  for (const bad of [
    null,
    undefined,
    'recording',
    {},
    { phase: 'paused' },
    { phase: 'toString' }, // inherited keys aren't phases
    { phase: '__proto__' },
    { phase: 'recording' }, // recording needs its start time
    { phase: 'recording', startedAt: 0 },
    { phase: 'recording', startedAt: -5 },
    { phase: 'recording', startedAt: Number.NaN },
    { phase: 'recording', startedAt: Infinity },
    { phase: 'recording', startedAt: '1800000000000' },
  ]) {
    assert.equal(parseReport(bad), null, JSON.stringify(bad))
  }
})

test('idle: Record Screen…, and nothing that says it is recording', () => {
  const view = trayView(IDLE, T0)
  assert.deepEqual(actions(view), ['record', 'show', 'quit'])
  assert.equal(view.recording, false)
  assert.equal(view.title, '')
})

test('recording: the red icon, the elapsed time beside it, and Stop', () => {
  const view = trayView({ phase: 'recording', startedAt: T0 }, T0 + 65_400)
  assert.equal(view.recording, true)
  assert.equal(view.title, '1:05')
  assert.deepEqual(actions(view), ['stop', 'show', 'quit'])
  assert.ok(!actions(view).includes('record'), 'no second recording while one runs')
})

test('while starting or stopping, neither Record nor Stop is offered', () => {
  for (const phase of ['starting', 'stopping', 'unfinished']) {
    const view = trayView({ phase, startedAt: T0 }, T0 + 1000)
    assert.deepEqual(actions(view), ['show', 'quit'], phase)
  }
  assert.equal(trayView({ phase: 'stopping', startedAt: T0 }, T0).recording, true) // still uploading
  assert.match(labels(trayView({ phase: 'unfinished', startedAt: 0 }, T0)).join(' '), /Recording stopped/)
})

test('a full start and stop from the menu bar', () => {
  // What the page reports, in order, for one recording started and stopped from the tray.
  const reports = [
    [{ phase: 'idle', startedAt: 0 }, ['record', 'show', 'quit']],
    [{ phase: 'requesting', startedAt: 0 }, ['show', 'quit']],
    [{ phase: 'recording', startedAt: T0 }, ['stop', 'show', 'quit']],
    [{ phase: 'stopping', startedAt: T0 }, ['show', 'quit']],
    [{ phase: 'idle', startedAt: 0 }, ['record', 'show', 'quit']], // the page left for the session
  ]
  for (const [report, expected] of reports) {
    assert.deepEqual(actions(trayView(parseReport(report), T0 + 5000)), expected, report.phase)
  }
})

test('closing the window hides it only while capture runs', () => {
  assert.equal(hideOnClose({ phase: 'recording', startedAt: T0 }), true)
  assert.equal(hideOnClose({ phase: 'stopping', startedAt: T0 }), true)
  for (const phase of ['idle', 'starting', 'unfinished']) assert.equal(hideOnClose({ phase, startedAt: 0 }), false, phase)
})

test('the clock reads like the Live page’s', () => {
  assert.equal(clock(0), '0:00')
  assert.equal(clock(9_999), '0:09')
  assert.equal(clock(605_000), '10:05')
  assert.equal(clock(3_723_000), '1:02:03')
  assert.equal(clock(-2000), '0:00') // a clock that moved back never shows a negative time
})

/** Run `events` through the relay from `state`; the commands sent and windows shown, in order. */
function run(events, state = RELAY_START) {
  const effects = []
  for (const event of events) {
    const out = relay(state, event)
    state = out.state
    if (out.send) effects.push(out.send)
    if (out.show) effects.push('show')
  }
  return { state, effects }
}
const report = (phase, startedAt = T0) => ({ type: 'report', report: { phase, startedAt } })
const RECORD = { type: 'record' }
const STOP = { type: 'stop' }
const LISTENING = { type: 'listening' }

test('Record with the window closed: one start, sent only once the UI listens', () => {
  const { state, effects } = run([
    RECORD,
    { type: 'pageGone' }, // the starting screen, then the UI, replace the page
    RECORD, // clicked twice while it loads
    LISTENING,
    LISTENING, // the Live page subscribes too
  ])
  assert.deepEqual(effects, ['start'])
  assert.equal(state.startPending, false)
})

test('Record with the UI already up starts at once', () => {
  assert.deepEqual(run([LISTENING, RECORD]).effects, ['start'])
})

test('a window closed before its UI loaded cancels the start', () => {
  const { state, effects } = run([RECORD, { type: 'windowClosed' }, LISTENING])
  assert.deepEqual(effects, [])
  assert.equal(state.startPending, false)
})

test('an engine that fails to start drops the waiting start, so Restart doesn’t record unasked', () => {
  assert.deepEqual(run([RECORD, { type: 'engineFailed' }, LISTENING]).effects, [])
})

test('no second start while one is under way or recording', () => {
  for (const phase of ['requesting', 'recording', 'stopping', 'unfinished']) {
    assert.deepEqual(run([LISTENING, report(phase), RECORD]).effects, [], phase)
  }
})

test('Stop is sent only to a page that records, and never queued for a later one', () => {
  assert.deepEqual(run([LISTENING, report('recording'), STOP]).effects, ['stop'])
  assert.deepEqual(run([LISTENING, STOP]).effects, [], 'not recording')
  assert.deepEqual(run([LISTENING, report('requesting'), STOP]).effects, [], 'still starting')
  // The page went away while recording: nothing to stop, now or after the next load.
  assert.deepEqual(run([LISTENING, report('recording'), { type: 'pageGone' }, STOP, LISTENING]).effects, [])
})

test('a page that goes away, or a closed window, resets the icon to idle', () => {
  for (const type of ['pageGone', 'windowClosed']) {
    const { state } = run([LISTENING, report('recording'), { type }])
    assert.deepEqual(state.recording, IDLE, type)
    assert.equal(state.listening, false, type)
    assert.equal(hideOnClose(state.recording), false, type)
  }
})

test('a stop that fails shows the window: it may be hidden, and the page offers Try again', () => {
  const failed = run([LISTENING, report('recording'), STOP, report('stopping'), report('unfinished')])
  assert.deepEqual(failed.effects, ['stop', 'show'])
  const done = run([LISTENING, report('recording'), STOP, report('stopping'), report('idle')])
  assert.deepEqual(done.effects, ['stop'], 'a stop that worked leaves the window alone')
  // Try again failing once more (finishing → unfinished) shows it again; it is in front already.
  assert.deepEqual(run([report('finishing'), report('unfinished')]).effects, ['show'])
})

test('a malformed report changes nothing and is flagged', () => {
  const recording = run([LISTENING, report('recording')]).state
  const out = relay(recording, { type: 'report', report: { phase: 'paused' } })
  assert.equal(out.state, recording)
  assert.equal(out.malformed, true)
})

test('a full recording from the menu bar, window closed meanwhile', () => {
  const { state, effects } = run([
    RECORD,
    LISTENING,
    report('requesting'),
    report('recording'),
    STOP,
    report('stopping'),
    report('idle'), // the page left for the session
  ])
  assert.deepEqual(effects, ['start', 'stop'])
  assert.deepEqual(state.recording, IDLE)
})
