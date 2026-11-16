const assert = require('node:assert/strict')
const { test } = require('node:test')
const { IDLE, RELAY_START, parseReport, trayView, clock, hideOnClose, holdsRecording, relay } = require('../src/tray.cjs')

const T0 = 1_800_000_000_000
const actions = (view) => view.items.filter((i) => i.id).map((i) => i.id)
const labels = (view) => view.items.filter((i) => i.label).map((i) => i.label)

test('the Live page’s phases map onto the tray’s', () => {
  assert.deepEqual(parseReport({ phase: 'idle' }), IDLE)
  assert.deepEqual(parseReport({ phase: 'requesting' }), { phase: 'starting', startedAt: 0, macSoundRefused: false, screen: false })
  assert.deepEqual(parseReport({ phase: 'recording', startedAt: T0, macSoundRefused: false, screen: false }), {
    phase: 'recording',
    startedAt: T0,
    macSoundRefused: false,
    screen: false,
  })
  assert.equal(parseReport({ phase: 'recording', startedAt: T0, macSoundRefused: false, screen: true }).screen, true)
  assert.equal(parseReport({ phase: 'stopped', startedAt: 0, macSoundRefused: false, screen: true }).screen, false)
  assert.equal(parseReport({ phase: 'recording', startedAt: T0, macSoundRefused: true, screen: false }).macSoundRefused, true)
  assert.deepEqual(parseReport({ phase: 'stopping', startedAt: T0 }), { phase: 'stopping', startedAt: 0, macSoundRefused: false, screen: false })
  assert.deepEqual(parseReport({ phase: 'finishing', startedAt: T0 }), { phase: 'stopping', startedAt: 0, macSoundRefused: false, screen: false })
  assert.deepEqual(parseReport({ phase: 'stopping', startedAt: 0 }), { phase: 'stopping', startedAt: 0, macSoundRefused: false, screen: false })
  assert.deepEqual(parseReport({ phase: 'unfinished', startedAt: T0 }), { phase: 'unfinished', startedAt: 0, macSoundRefused: false, screen: false })
})

test('a malformed report is refused, not guessed at', () => {
  for (const bad of [
    null,
    undefined,
    'recording',
    {},
    { phase: 'paused' },
    { phase: 'toString' },
    { phase: '__proto__' },
    { phase: 'recording' },
    { phase: 'recording', startedAt: 0 },
    { phase: 'recording', startedAt: -5 },
    { phase: 'recording', startedAt: Number.NaN },
    { phase: 'recording', startedAt: Infinity },
    { phase: 'recording', startedAt: '1800000000000', macSoundRefused: false },
    { phase: 'recording', startedAt: T0 },
    { phase: 'recording', startedAt: T0, macSoundRefused: 'no', screen: false },
    { phase: 'recording', startedAt: T0, macSoundRefused: false },
    { phase: 'recording', startedAt: T0, macSoundRefused: false, screen: 'yes' },
  ]) {
    assert.equal(parseReport(bad), null, JSON.stringify(bad))
  }
})

test('idle: Record Audio and Record Screen…, and nothing that says it is recording', () => {
  const view = trayView(IDLE, T0)
  assert.deepEqual(actions(view), ['recordAudio', 'recordScreen', 'show', 'quit'])
  assert.equal(view.recording, false)
  assert.equal(view.title, '')
})

test('recording: the red icon, the elapsed time beside it, Share Screen… and Stop', () => {
  const view = trayView({ phase: 'recording', startedAt: T0, screen: false }, T0 + 65_400)
  assert.equal(view.recording, true)
  assert.equal(view.title, '1:05')
  assert.deepEqual(actions(view), ['shareScreen', 'stop', 'show', 'quit'])
  assert.ok(labels(view).includes('Share Screen…'))
  const sharing = trayView({ phase: 'recording', startedAt: T0, screen: true }, T0)
  assert.deepEqual(actions(sharing), ['stopSharing', 'stop', 'show', 'quit'])
  assert.ok(labels(sharing).includes('Stop Sharing'))
  assert.ok(!actions(view).some((a) => a.startsWith('record')), 'no second recording while one runs')
})

test('while starting or stopping, neither Record nor Stop is offered', () => {
  for (const phase of ['starting', 'stopping', 'unfinished']) {
    const view = trayView({ phase, startedAt: T0 }, T0 + 1000)
    assert.deepEqual(actions(view), ['show', 'quit'], phase)
  }
  assert.equal(trayView({ phase: 'stopping', startedAt: T0 }, T0).recording, true)
  assert.match(labels(trayView({ phase: 'unfinished', startedAt: 0 }, T0)).join(' '), /Recording stopped/)
})

test('a full start and stop from the menu bar', () => {
  const reports = [
    [{ phase: 'idle', startedAt: 0 }, ['recordAudio', 'recordScreen', 'show', 'quit']],
    [{ phase: 'requesting', startedAt: 0 }, ['show', 'quit']],
    [{ phase: 'recording', startedAt: T0, macSoundRefused: false, screen: true }, ['stopSharing', 'stop', 'show', 'quit']],
    [{ phase: 'recording', startedAt: T0, macSoundRefused: false, screen: false }, ['shareScreen', 'stop', 'show', 'quit']],
    [{ phase: 'stopping', startedAt: T0 }, ['show', 'quit']],
    [{ phase: 'idle', startedAt: 0 }, ['recordAudio', 'recordScreen', 'show', 'quit']],
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
  assert.equal(clock(-2000), '0:00')
})

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
const report = (phase, startedAt = T0, macSoundRefused = false, screen = false) => ({
  type: 'report',
  report: { phase, startedAt, macSoundRefused, screen },
})
const RECORD = { type: 'record', mode: 'screen' }
const RECORD_AUDIO = { type: 'record', mode: 'audio' }
const STOP = { type: 'stop' }
const LISTENING = { type: 'listening' }

test('Record with the window closed: one start, sent only once the UI listens', () => {
  const { state, effects } = run([
    RECORD,
    { type: 'pageGone' },
    RECORD,
    LISTENING,
    LISTENING,
  ])
  assert.deepEqual(effects, ['start-screen'])
  assert.equal(state.startPending, null)
})

test('Record with the UI already up starts at once', () => {
  assert.deepEqual(run([LISTENING, RECORD]).effects, ['start-screen'])
})

test('a window closed before its UI loaded cancels the start', () => {
  const { state, effects } = run([RECORD, { type: 'windowClosed' }, LISTENING])
  assert.deepEqual(effects, [])
  assert.equal(state.startPending, null)
})

test('an engine that fails to start drops the waiting start, so Restart doesn’t record unasked', () => {
  assert.deepEqual(run([RECORD, { type: 'engineFailed' }, LISTENING]).effects, ['show'])
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
    report('idle'),
  ])
  assert.deepEqual(effects, ['start-screen', 'stop'])
  assert.deepEqual(state.recording, IDLE)
})

test('a Live page left while its upload is still retrying keeps Iris busy until it settles', () => {
  const left = run([LISTENING, report('recording'), report('stopping'), report('unfinished'), report('stopping', 0)])
  assert.equal(left.state.recording.phase, 'stopping')
  assert.equal(hideOnClose(left.state.recording), true, 'closing the window would lose the queued chunks')
  assert.equal(run([report('idle', 0)], left.state).state.recording.phase, 'idle')
})

const CANCELLED = { type: 'pickerCancelled' }

test('Record from the menu bar shows only the picker: the window stays as it was', () => {
  assert.deepEqual(run([RECORD, LISTENING, report('requesting'), report('recording')]).effects, ['start-screen'])
  const { state } = run([LISTENING, RECORD, report('requesting'), report('recording')])
  assert.equal(state.quietStart, false, 'done once it records')
})

test('a cancel in the picker shows nothing; a start that fails otherwise shows the window', () => {
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), CANCELLED, report('idle', 0)]).effects, ['start-screen'])
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), report('idle', 0)]).effects, ['start-screen', 'show'])
})

test("a start from the Live page's own button never shows the window: it is in front already", () => {
  assert.deepEqual(run([LISTENING, report('requesting'), report('idle', 0)]).effects, [])
})

test('an idle report before the start reaches the page keeps the start quiet until it ends', () => {
  const { effects } = run([RECORD, LISTENING, report('idle', 0), report('requesting'), report('idle', 0)])
  assert.deepEqual(effects, ['start-screen', 'show'])
})

test('a failed start is shown once: the next start from the page itself is not quiet', () => {
  const failed = run([LISTENING, RECORD, report('requesting'), report('idle', 0)]).state
  assert.deepEqual(run([report('requesting'), report('idle', 0)], failed).effects, [])
})

test('an engine that fails while a menu bar start waits shows the window its dialog belongs to', () => {
  assert.deepEqual(run([RECORD, { type: 'engineFailed' }]).effects, ['show'])
  assert.deepEqual(run([LISTENING, { type: 'engineFailed' }]).effects, [], 'no start waiting: nothing to show')
})

test('a page that goes away or a closed window ends a quiet start', () => {
  for (const type of ['pageGone', 'windowClosed']) {
    const { state } = run([LISTENING, RECORD, report('requesting'), { type }])
    assert.equal(state.quietStart, false, type)
  }
})

test('a quiet start the page never answers shows the window: it is held up by a question it asks', () => {
  const UNANSWERED = { type: 'startUnanswered' }
  assert.deepEqual(run([LISTENING, RECORD, UNANSWERED]).effects, ['start-screen', 'show'])
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), UNANSWERED]).effects, ['start-screen'])
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), report('recording'), UNANSWERED]).effects, ['start-screen'])
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), report('idle', 0), UNANSWERED]).effects, ['start-screen', 'show'])
  assert.deepEqual(run([LISTENING, RECORD, report('requesting'), CANCELLED, report('idle', 0), UNANSWERED]).effects, ['start-screen'])
  assert.deepEqual(run([LISTENING, report('requesting'), report('idle', 0), UNANSWERED]).effects, [])
})

test('Record Audio starts an audio recording, also once the UI loads, with no window shown', () => {
  assert.deepEqual(run([LISTENING, RECORD_AUDIO, report('requesting'), report('recording')]).effects, ['start-audio'])
  assert.deepEqual(run([RECORD_AUDIO, LISTENING]).effects, ['start-audio'])
  assert.deepEqual(run([LISTENING, RECORD_AUDIO, report('requesting'), report('idle', 0)]).effects, [
    'start-audio',
    'show',
  ])
  assert.deepEqual(run([LISTENING, { type: 'record', mode: 'video' }]).effects, [], 'an unknown mode starts nothing')
})

test("macOS refusing the Mac's sound shows the window once: the page says how to allow it", () => {
  const refused = report('recording', T0, true)
  const atStart = run([LISTENING, RECORD_AUDIO, report('requesting'), refused, refused])
  assert.deepEqual(atStart.effects, ['start-audio', 'show'])
  assert.deepEqual(run([LISTENING, report('requesting'), report('recording'), refused]).effects, ['show'])
  assert.deepEqual(run([report('stopping'), report('idle', 0), report('recording'), refused], atStart.state).effects, [
    'show',
  ])
})

test('stopped: not red, and the four choices for the recording', () => {
  assert.deepEqual(parseReport({ phase: 'stopped', startedAt: 0 }), { phase: 'stopped', startedAt: 0, macSoundRefused: false, screen: false })
  const view = trayView({ phase: 'stopped', startedAt: 0 }, T0)
  assert.equal(view.recording, false)
  assert.deepEqual(actions(view), ['continue', 'makeNotes', 'startOver', 'discard', 'show', 'quit'])
  assert.equal(hideOnClose({ phase: 'stopped', startedAt: 0 }), true, 'closing the window would end the recording')
  assert.deepEqual(labels(trayView({ phase: 'recording', startedAt: T0 }, T0)).slice(0, 3), ['Recording', 'Share Screen…', 'Stop'])
})

test("a stopped recording's choices reach the page; the ones that delete it show the window", () => {
  const stopped = run([LISTENING, report('recording'), STOP, report('stopped', 0)])
  assert.deepEqual(stopped.effects, ['stop'])
  const choose = (choice) => run([{ type: 'choice', choice }], stopped.state).effects
  assert.deepEqual(choose('continue'), ['continue'])
  assert.deepEqual(choose('makeNotes'), ['make-notes'])
  assert.deepEqual(choose('startOver'), ['start-over', 'show'])
  assert.deepEqual(choose('discard'), ['discard', 'show'])
  assert.deepEqual(choose('toString'), [], 'only known choices')
  assert.deepEqual(run([LISTENING, report('recording'), { type: 'choice', choice: 'discard' }]).effects, [])
  assert.deepEqual(run([{ type: 'pageGone' }, { type: 'choice', choice: 'continue' }], stopped.state).effects, [])
})

test('a Continue from the menu bar is quiet: the window shows only if it fails, not for a cancel', () => {
  const stopped = run([LISTENING, report('recording'), STOP, report('stopped', 0)]).state
  const CONTINUE = { type: 'choice', choice: 'continue' }
  assert.deepEqual(run([CONTINUE, report('continuing', 0), report('recording')], stopped).effects, ['continue'])
  assert.deepEqual(run([CONTINUE, report('continuing', 0), report('stopped', 0)], stopped).effects, ['continue', 'show'])
  assert.deepEqual(run([CONTINUE, report('continuing', 0), CANCELLED, report('stopped', 0)], stopped).effects, ['continue'])
})

test("the Mac's sound refused brings the window up once per recording, not after each Continue", () => {
  const refused = (phase, startedAt = T0) => ({
    type: 'report',
    report: { phase, startedAt, macSoundRefused: true, screen: false },
  })
  const { effects } = run([
    LISTENING,
    report('requesting'),
    refused('recording'),
    refused('stopped', 0),
    refused('continuing', 0),
    refused('recording'),
  ])
  assert.deepEqual(effects, ['show'])
})

test('closing the window while a Continue asks hides it: the recording is still open', () => {
  const continuing = parseReport({ phase: 'continuing', startedAt: 0 })
  assert.equal(continuing.phase, 'continuing')
  assert.equal(hideOnClose(continuing), true)
  assert.deepEqual(actions(trayView(continuing, T0)), ['show', 'quit'])
  assert.match(labels(trayView(continuing, T0)).join(' '), /Continuing/)
})

test('only a page holding a recording makes quitting ask; a start asking for permissions holds none', () => {
  const held = (phase) => holdsRecording(parseReport({ phase, startedAt: T0, macSoundRefused: false, screen: false }))
  assert.equal(held('idle'), false)
  assert.equal(held('requesting'), false, 'asking for the screen or the mic: nothing is recorded yet')
  for (const phase of ['recording', 'stopped', 'continuing', 'stopping', 'finishing', 'unfinished']) {
    assert.equal(held(phase), true, phase)
  }
})

test('Share Screen… and Stop Sharing reach a recording page, only as a change, never queued', () => {
  const SHARE = { type: 'screen', on: true }
  const UNSHARE = { type: 'screen', on: false }
  assert.deepEqual(run([SHARE]).effects, [])
  assert.deepEqual(run([LISTENING, report('stopped', 0), SHARE]).effects, [])
  assert.deepEqual(run([LISTENING, report('recording'), SHARE]).effects, ['share-screen'])
  assert.deepEqual(run([LISTENING, report('recording', T0, false, true), UNSHARE]).effects, ['stop-sharing'])
  assert.deepEqual(run([LISTENING, report('recording', T0, false, true), SHARE]).effects, [])
  assert.deepEqual(run([LISTENING, report('recording'), UNSHARE]).effects, [])
  assert.ok(!run([LISTENING, report('recording'), SHARE]).effects.includes('show'))
})

test("leaving recording closes a Share Screen picker still open, so it can't hold up the next request", () => {
  const closes = (events) => events.reduce((acc, e) => {
    const out = relay(acc.state, e)
    return { state: out.state, closed: acc.closed || Boolean(out.closePicker) }
  }, { state: RELAY_START, closed: false }).closed
  assert.equal(closes([LISTENING, report('recording'), report('stopped', 0)]), true)
  assert.equal(closes([LISTENING, report('recording'), report('stopping', 0)]), true)
  assert.equal(closes([LISTENING, report('requesting', 0), report('recording')]), false)
  assert.equal(closes([LISTENING, report('stopped', 0), report('continuing', 0), report('recording')]), false)
  assert.equal(closes([LISTENING, report('recording'), report('recording', T0, false, true)]), false)
})
