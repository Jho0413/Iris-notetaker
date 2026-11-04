import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent, Notes, Session } from '../src/api/client.ts'
import {
  announcement, currentSteps, emptyRun, eventKey, failureView, figureList, formatClock,
  formatDuration, GENERIC_FAILURE, knownFailureMessage, libraryState, liveProgress, progressView,
  provenance, reduceEvents, runSeconds, sessionLength, STAGE_ORDER, stageDetail, stageSeconds,
  stepLine, stepStatus,
  transcriptSegments, uiState, type UiStateKind,
} from '../src/session/state.ts'
import { STAGES } from '../src/api/client.ts'

let tick = 0
function ev(type: IrisEvent['type'], data: Record<string, unknown> = {}): IrisEvent {
  tick += 1
  return { type, session_id: 's1', data, at: new Date(Date.UTC(2026, 10, 3, 12, 0, tick)).toISOString() }
}

const session: Session = {
  id: 's1', title: 'Algebra', source: 'upload', status: 'processing', current_stage: null,
  failure_message: null, failure_kind: null, duration_s: null, created_at: '2026-11-03T12:00:00Z', updated_at: '2026-11-03T12:00:00Z',
}

test('stages move through running and done, with durations from event times', () => {
  const start = ev('status', { status: 'processing' })
  const started = ev('stage_started', { stage: 'ingest' })
  const finished = ev('stage_finished', { stage: 'ingest' })
  const run = reduceEvents([start, started, finished,
    ev('stage_started', { stage: 'transcribe' })])
  assert.equal(run.stages.ingest.status, 'done')
  assert.equal(stageSeconds(run.stages.ingest), 1)
  assert.equal(run.stages.transcribe.status, 'running')
  assert.equal(run.stages.detect.status, 'pending')
  assert.equal(uiState(session, run).label, 'Transcribing')
})

test('stages promoted at stop are done, marked live and not timed', () => {
  const promoted = (type: 'stage_started' | 'stage_finished', stage: string) =>
    ev(type, { stage, promoted: true })
  const events = [
    ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'ingest' }), ev('stage_finished', { stage: 'ingest' }),
    promoted('stage_started', 'transcribe'), promoted('stage_finished', 'transcribe'),
    promoted('stage_started', 'detect'),
    ev('moment_found', { id: 'm1', start: 0, end: 9 }),
    ev('moment_found', { id: 'm2', start: 9, end: 20 }),
    promoted('stage_finished', 'detect'),
    ev('stage_started', { stage: 'write' }),
  ]
  const run = reduceEvents(events)
  assert.equal(run.stages.transcribe.status, 'done')
  assert.equal(run.stages.transcribe.promoted, true)
  assert.equal(stageSeconds(run.stages.transcribe), undefined)
  assert.equal(stageDetail('transcribe', run), 'live')
  assert.equal(stageDetail('detect', run), '2 moments · live')
  assert.equal(run.stages.ingest.promoted, undefined)
  assert.equal(stageSeconds(run.stages.ingest), 1)
  assert.equal(run.stages.write.status, 'running')
  // The next run starts fresh: a stage that runs there is no longer marked live.
  const next = reduceEvents([...events, ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'transcribe' })])
  assert.equal(next.stages.transcribe.promoted, undefined)
})

test('a promoted transcript is counted from the backend, not from the parts seen since stop', () => {
  const chunk = (i: number) => ev('transcript_chunk', { chunk: i, segments: [{ start: i, end: i + 1, text: 'x' }] })
  const events = [
    // Recording: the live audio lane's windows.
    ...[0, 1, 2, 3].map(chunk),
    // Stop: this run's counts reset, then the window cut at stop arrives.
    ev('status', { status: 'processing' }),
    chunk(4),
    ev('stage_started', { stage: 'transcribe', promoted: true }),
    ev('stage_finished', { stage: 'transcribe', promoted: true, segments: 412 }),
  ]
  const run = reduceEvents(events)
  assert.equal(stageDetail('transcribe', run), '412 segments · live')
  // One segment, none (said nothing) and a malformed count.
  const one = reduceEvents([...events.slice(0, -1), ev('stage_finished', { stage: 'transcribe', promoted: true, segments: 1 })])
  assert.equal(stageDetail('transcribe', one), '1 segment · live')
  const none = reduceEvents([...events.slice(0, -1), ev('stage_finished', { stage: 'transcribe', promoted: true, segments: 0 })])
  assert.equal(stageDetail('transcribe', none), 'live')
  const bad = reduceEvents([...events.slice(0, -1), ev('stage_finished', { stage: 'transcribe', promoted: true, segments: 'many' })])
  assert.equal(stageDetail('transcribe', bad), '1 part · live')
  // A later run that transcribes for real counts its parts again.
  const rerun = reduceEvents([...events, ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'transcribe' }), chunk(0), chunk(1), ev('stage_finished', { stage: 'transcribe' })])
  assert.equal(stageDetail('transcribe', rerun), '2 parts')
})

test('a promotion that fails is shown as failed, not as done live', () => {
  const run = reduceEvents([
    ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'select', promoted: true }),
    ev('visual_selected', { id: 'v1', caption: 'Graph', frame: { t: 4 } }),
    ev('error', { message: GENERIC_FAILURE, kind: 'general', stage: 'select' }),
  ])
  assert.equal(run.stages.select.status, 'failed')
  assert.equal(stageDetail('select', run), '1 figure')
  assert.equal(stageSeconds(run.stages.select), undefined)
})

test('replayed history changes nothing', () => {
  const events = [
    ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'transcribe' }),
    ev('transcript_chunk', { chunk: 1, segments: [{ start: 5, end: 9, text: 'second' }] }),
    ev('transcript_chunk', { chunk: 0, segments: [{ start: 0, end: 5, text: 'first' }] }),
    ev('visual_selected', { id: 'v1', caption: 'Graph', frame: { t: 4, path: 'frames/1.jpg' } }),
    ev('visual_rendered', { id: 'v1', path: 'visuals/v1.svg', render_as: 'svg', verified: true }),
    ev('notes_updated', { final: false, completed_parts: 1, total_parts: 3 }),
  ]
  const once = reduceEvents(events)
  assert.deepEqual(reduceEvents([...events, ...events]), once)
  assert.equal(new Set([...events, ...events].map(eventKey)).size, events.length)
})

test('transcript chunks arriving out of order are shown in time order', () => {
  const run = reduceEvents([
    ev('transcript_chunk', { chunk: 2, segments: [{ start: 20, end: 30, text: 'c' }] }),
    ev('transcript_chunk', { chunk: 0, segments: [{ start: 0, end: 10, text: 'a' }] }),
    ev('transcript_chunk', { chunk: 1, segments: [{ start: 10, end: 20, text: 'b' }, { bad: 1 }] }),
  ])
  assert.deepEqual(transcriptSegments(run, null).map((s) => s.text), ['a', 'b', 'c'])
  assert.equal(stageDetail('transcribe', run), '3 parts')
  const full = { language: 'en', segments: [{ start: 0, end: 30, text: 'all' }] }
  assert.deepEqual(transcriptSegments(run, full).map((s) => s.text), ['all'])
})

test('a figure stays rendered when its selection is replayed afterwards', () => {
  const selected = ev('visual_selected', { id: 'v1', caption: 'Orbit', frame: { t: 12 } })
  const rendered = ev('visual_rendered', { id: 'v1', path: 'visuals/v1.png', render_as: 'image',
    verified: true })
  const run = reduceEvents([selected, rendered, selected,
    ev('visual_selected', { id: 'v2', caption: 'Table', frame: { t: 40 } })])
  assert.equal(run.figures.v1.rendered, true)
  assert.equal(run.figures.v1.path, 'visuals/v1.png')
  assert.equal(run.figures.v1.caption, 'Orbit')
  assert.deepEqual(run.figureOrder, ['v1', 'v2'])
  assert.equal(stageDetail('render', run), '1 of 2')
})

test('finished notes and run events merge into one figure list', () => {
  const notes = {
    session_id: 's1', session_type: 'lecture', title: 'Algebra', markdown: '',
    visuals: [{ id: 'v1', moment_id: 'm1', frame: { t: 12, path: 'frames/1.jpg' }, caption: 'Orbit',
      path: 'visuals/v1.jpg', render_as: 'screenshot' }],
  } as Notes
  const run = reduceEvents([ev('visual_rendered', { id: 'v1', path: 'visuals/v1.jpg',
    render_as: 'screenshot', verified: false })])
  const [fig] = figureList(run, notes)
  assert.equal(fig.verified, false)
  assert.equal(fig.t, 12)
  assert.equal(figureList(emptyRun(), notes)[0].verified, undefined) // unknown without events
  // A redraw in the finished notes passed its check, events or not.
  const redrawn = { ...notes, visuals: [{ ...notes.visuals[0], render_as: 'svg' as const }] }
  assert.equal(figureList(emptyRun(), redrawn)[0].verified, true)
  assert.equal(provenance(figureList(emptyRun(), redrawn)[0]), 'verified')
  // A screenshot in older notes may be a fallback crop: no label without this run's events.
  assert.equal(provenance(figureList(emptyRun(), notes)[0]), undefined)
  // Notes now say how each figure was made: an original image is labelled as such, events or not.
  const original = { ...notes, visuals: [{ ...notes.visuals[0], verified: false }] }
  assert.equal(provenance(figureList(emptyRun(), original)[0]), 'unverified')
  const requested = { ...notes, visuals: [{ ...notes.visuals[0], verified: true }] }
  assert.equal(provenance(figureList(emptyRun(), requested)[0]), 'screenshot')
  const shot = reduceEvents([ev('visual_rendered', { id: 'v1', path: 'visuals/v1.jpg',
    render_as: 'screenshot', verified: true })])
  assert.equal(provenance(figureList(shot, notes)[0]), 'screenshot')
  assert.equal(provenance(fig), 'unverified')
  assert.equal(provenance({ id: 'v9', caption: '', rendered: false }), undefined)
})

test('a new run clears the previous run, and replays of its start do not', () => {
  const run1 = [ev('status', { status: 'processing' }), ev('stage_started', { stage: 'write' }),
    ev('error', { message: GENERIC_FAILURE, kind: 'general', stage: 'write' }),
    ev('status', { status: 'failed' })]
  const failed = reduceEvents(run1)
  assert.equal(failed.stages.write.status, 'failed')
  assert.equal(uiState({ ...session, status: 'failed' }, failed).fromStage, 'write')

  const start2 = ev('status', { status: 'processing' })
  const run2 = reduceEvents([...run1, start2, ev('stage_started', { stage: 'write' }), start2])
  assert.equal(run2.status, 'processing')
  assert.equal(run2.failure, undefined)
  assert.equal(run2.stages.write.status, 'running')
})

test('resuming after a backend restart starts clean, even without a failed status between', () => {
  const old = [ev('status', { status: 'processing' }), ev('stage_started', { stage: 'write' }),
    ev('stage_finished', { stage: 'write' }), ev('stage_started', { stage: 'render' }),
    ev('notes_updated', { final: false, completed_parts: 3, total_parts: 4 }),
    ev('visual_selected', { id: 'v1', caption: 'a' })]
  const fresh = [ev('status', { status: 'processing' }), ev('stage_started', { stage: 'write' })]
  const resumed = reduceEvents([...old, ...fresh])
  assert.deepEqual(resumed, reduceEvents(fresh))
  assert.equal(resumed.stages.write.status, 'running')
  // Replaying everything, both runs included, still lands on the latest run.
  assert.deepEqual(reduceEvents([...old, ...fresh, ...old, ...fresh]), resumed)
})

test('a failure in a parallel group marks only the named stage failed', () => {
  const run = reduceEvents([ev('stage_started', { stage: 'write' }),
    ev('stage_started', { stage: 'render' }),
    ev('error', { message: GENERIC_FAILURE, kind: 'general', stage: 'render' })])
  assert.equal(run.stages.render.status, 'failed')
  assert.equal(run.stages.write.status, 'stopped')
  const unnamed = reduceEvents([ev('stage_started', { stage: 'align' }),
    ev('error', { message: GENERIC_FAILURE, kind: 'general' })])
  assert.equal(unnamed.stages.align.status, 'failed')
})

test('UI states cover recording, processing, polishing, stalled, finished and failed', () => {
  // Sections written are progress, not a readable draft: the state stays processing.
  const writing = reduceEvents([ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'write' }),
    ev('notes_updated', { final: false, completed_parts: 2, total_parts: 4 })])
  assert.deepEqual(uiState(session, writing),
    { kind: 'processing', label: 'Writing notes · 2 of 4', stage: 'write' })
  const final = reduceEvents([ev('notes_updated', { final: false, completed_parts: 4,
    total_parts: 4 }), ev('notes_updated', { final: true })])
  assert.equal(final.notes?.completed, 4)
  // Assembled, then polished: still not shown until done.
  assert.deepEqual(uiState(session, final),
    { kind: 'polishing', label: 'Polishing notes', stage: 'synthesize' })

  const rec = { ...session, status: 'recording' as const }
  assert.equal(uiState(rec, emptyRun(), { capturing: true }).kind, 'recording')
  assert.equal(uiState(rec, emptyRun()).kind, 'recording-orphan')
  const stalled = uiState({ ...session, current_stage: 'select' }, emptyRun(), { stalled: true })
  assert.deepEqual([stalled.kind, stalled.fromStage], ['stalled', 'select'])
  assert.equal(uiState({ ...session, status: 'created' }, emptyRun()).kind, 'starting')
  assert.equal(uiState({ ...session, status: 'done' }, emptyRun()).kind, 'finished')

  // The latest status event wins over a stale stored session.
  const done = reduceEvents([ev('status', { status: 'done' })])
  assert.equal(uiState(session, done).kind, 'finished')
})

test('the synthesis pass is the last step, after the notes are assembled', () => {
  assert.equal(STAGE_ORDER[STAGE_ORDER.length - 1], 'synthesize')
  assert.deepEqual(STAGES, [...STAGE_ORDER])
  const run = reduceEvents([
    ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'assemble' }),
    ev('notes_updated', { final: true }),
    ev('stage_finished', { stage: 'assemble' }),
    ev('stage_started', { stage: 'synthesize' }),
  ])
  assert.equal(run.stages.assemble.status, 'done')
  assert.equal(run.stages.synthesize.status, 'running')
  assert.equal(uiState(session, run).kind, 'polishing')
  // Synthesis re-assembles: a second final notes event changes nothing but stays polishing.
  const again = reduceEvents([ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'synthesize' }), ev('notes_updated', { final: true }),
    ev('stage_finished', { stage: 'synthesize' })])
  assert.equal(again.stages.synthesize.status, 'done')
  assert.equal(uiState(session, again).kind, 'polishing')
  const done = reduceEvents([ev('status', { status: 'processing' }),
    ev('stage_finished', { stage: 'synthesize' }), ev('status', { status: 'done' })])
  assert.equal(uiState(session, done).kind, 'finished')
  // A stalled synthesis resumes from synthesis; a library row names the step.
  const stalled = uiState({ ...session, current_stage: 'synthesize' }, emptyRun(), { stalled: true })
  assert.equal(stalled.fromStage, 'synthesize')
  assert.equal(libraryState({ ...session, current_stage: 'synthesize' }).label, 'Polishing notes')
  assert.equal(stageDetail('synthesize', run), undefined)
})

test('library rows use the stored session only', () => {
  const failed = { ...session, status: 'failed' as const, current_stage: 'ingest' as const,
    failure_message: GENERIC_FAILURE, failure_kind: 'general' as const }
  assert.equal(libraryState(failed).label, 'Failed · Preparing the recording')
  assert.equal(libraryState({ ...failed, failure_message: null }).fromStage, 'ingest')
  assert.equal(libraryState({ ...session, current_stage: 'render' }).label, 'Redrawing figures')
  assert.equal(libraryState({ ...session, current_stage: 'render' }).kind, 'processing')
  assert.equal(libraryState(session).label, 'Processing')
  assert.equal(libraryState({ ...session, status: 'done' }).label, 'Finished')
})

test('while processing, the label is the running step and its count', () => {
  const run = reduceEvents([
    ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'write' }),
    ev('stage_started', { stage: 'render' }),
    ev('visual_selected', { id: 'v1', caption: 'A' }),
    ev('visual_selected', { id: 'v2', caption: 'B' }),
    ev('visual_selected', { id: 'v3', caption: 'C' }),
    ev('visual_rendered', { id: 'v1', path: 'visuals/v1.svg' }),
    ev('visual_rendered', { id: 'v2', path: 'visuals/v2.svg' }),
    ev('notes_updated', { final: false, completed_parts: 1, total_parts: 3 }),
  ])
  // Write and render run side by side: the chip names the later one, the page shows both.
  assert.deepEqual(currentSteps(session, run), ['write', 'render'])
  assert.equal(uiState(session, run).label, 'Redrawing figures · 2 of 3')
  assert.equal(stepLine('write', run), 'Writing notes · 1 of 3')
  assert.equal(stepLine('assemble', run), 'Putting the notes together')
  // Counts are never announced one by one, only the state and step.
  assert.equal(announcement(uiState(session, run)), 'Processing · Redrawing figures')
  assert.equal(announcement(uiState({ ...session, status: 'done' }, emptyRun())), 'Finished')
  // Between two steps (or without events) the stored step stands in.
  assert.deepEqual(currentSteps({ ...session, current_stage: 'align' }, emptyRun()), ['align'])
  assert.deepEqual(currentSteps(session, emptyRun()), [])
})

test('the progress view replaces the notes in every state but finished', () => {
  const views = Object.fromEntries(
    (['starting', 'processing', 'polishing', 'stalled', 'failed', 'recording-orphan', 'finished'] as
      UiStateKind[]).map((k) => [k, progressView(k)]),
  )
  assert.equal(views.finished, null)
  assert.deepEqual(views.processing, { heading: 'Making your notes', active: true })
  assert.deepEqual(views.polishing, { heading: 'Polishing your notes', active: true })
  assert.equal(views.starting?.active, true)
  // Failed, stalled and orphaned: the banner says what to do; no timer or step line.
  for (const k of ['failed', 'stalled', 'recording-orphan']) {
    assert.equal(views[k]?.active, false, k)
    assert.ok(views[k]?.heading, k)
  }
  // A re-run of a finished session is processing again, so its old notes are hidden too.
  const rerun = reduceEvents([ev('status', { status: 'done' }), ev('status', { status: 'processing' })])
  assert.notEqual(progressView(uiState({ ...session, status: 'done' }, rerun).kind), null)
})

test('the run time counts from the run start', () => {
  const run = reduceEvents([ev('status', { status: 'processing' })])
  assert.equal(runSeconds(run, run.runStart! + 65_000), 65)
  assert.equal(runSeconds(run, run.runStart! - 5), 0)
  assert.equal(runSeconds(emptyRun(), Date.now()), undefined)
})

test('step statuses fall back to the stored session without events', () => {
  const stored = { finished: false, hasEvents: false, currentStage: 'select' as const }
  assert.equal(stepStatus('align', emptyRun(), stored), 'done')
  assert.equal(stepStatus('select', emptyRun(), stored), 'pending')
  assert.equal(stepStatus('write', emptyRun(), { ...stored, finished: true }), 'done')
  // Events win, even a pending step.
  const run = reduceEvents([ev('stage_started', { stage: 'ingest' })])
  assert.equal(stepStatus('ingest', run, { ...stored, hasEvents: true }), 'running')
  assert.equal(stepStatus('align', run, { ...stored, hasEvents: true }), 'pending')
})

test('the live progress line shows only what is non-zero', () => {
  const events: IrisEvent[] = []
  const line = () => liveProgress(reduceEvents(events))
  assert.equal(line(), '')
  events.push(ev('transcript_chunk', { chunk: 0, segments: [{ start: 0, end: 9.5, text: 'hi' }] }))
  assert.equal(line(), 'Transcribed to 0:09')
  events.push(ev('moment_found', { id: 'm1', start: 0, end: 30 }))
  assert.equal(line(), 'Transcribed to 0:09 · 1 screen')
  events.push(ev('transcript_chunk', { chunk: 1, segments: [{ start: 9.5, end: 270.2, text: 'x' }] }))
  events.push(ev('moment_found', { id: 'm2', start: 30, end: 60 }))
  events.push(ev('moment_found', { id: 'm3', start: 60, end: 90 }))
  events.push(ev('visual_selected', { id: 'v1', caption: 'A' }))
  assert.equal(line(), 'Transcribed to 4:30 · 3 screens · 1 figure read')
  events.push(ev('visual_selected', { id: 'v2', caption: 'B' }))
  events.push(ev('notes_updated', { final: false, completed_parts: 1, live: true }))
  assert.equal(line(), 'Transcribed to 4:30 · 3 screens · 2 figures read · 1 section written')
  events.push(ev('notes_updated', { final: false, completed_parts: 2, live: true }))
  assert.match(line(), /2 sections written$/)
  // Replayed history (a reconnect) changes nothing.
  assert.equal(liveProgress(reduceEvents([...events, ...events])), line())
  // Sections can be written before anything is on screen.
  assert.equal(liveProgress(reduceEvents([ev('notes_updated', { final: false, completed_parts: 1,
    live: true })])), '1 section written')
})

test('an unparseable timestamp never blocks later runs', () => {
  const bad = { ...ev('status', { status: 'processing' }), at: 'not a time' }
  const run = reduceEvents([bad, ev('status', { status: 'failed' }), ev('status', { status: 'processing' }),
    ev('stage_started', { stage: 'write' })])
  assert.equal(run.status, 'processing')
  assert.equal(run.stages.write.status, 'running')
})

test('malformed event data is ignored', () => {
  const run = reduceEvents([
    ev('stage_started', { stage: 'nope' }), ev('status', { status: 'weird' }),
    ev('transcript_chunk', { chunk: 'x' }), ev('visual_rendered', { id: 'v1' }),
    ev('moment_found', { id: 'm1' }),
  ])
  assert.deepEqual(run, emptyRun())
})

test('formatting and error helpers', () => {
  assert.equal(formatClock(0), '0:00')
  assert.equal(formatClock(65.9), '1:05')
  assert.equal(formatClock(3729), '1:02:09')
  assert.equal(formatDuration(0.84), '0.8 s')
  assert.equal(formatDuration(42.4), '42 s')
  assert.equal(formatDuration(185), '3 min 05 s')
  const segs = [{ start: 0, end: 61.5, text: 'a' }]
  assert.equal(sessionLength(session, segs), 61.5)
  assert.equal(sessionLength(session, []), undefined)
  assert.equal(sessionLength({ ...session, duration_s: 90 }, segs), 90)
})

// --- Failures: only our own messages ------------------------------------------------------------

const SECRET = 'render failed: APIConnectionError: SECRET-sk-proj-42 openai at api.openai.com'

function failedSession(patch: Partial<Session>): Session {
  return { ...session, status: 'failed', current_stage: 'render', ...patch }
}

function shown(view: object): string {
  return JSON.stringify(view)
}

test('a general failure shows the generic message, the step and Try again', () => {
  const view = failureView(failedSession({ failure_message: GENERIC_FAILURE, failure_kind: 'general' }), emptyRun())
  assert.deepEqual(view, {
    message: GENERIC_FAILURE, kind: 'general', stage: 'render', headline: 'Stopped at Redrawing figures',
  })
  const ui = uiState(failedSession({ failure_message: GENERIC_FAILURE, failure_kind: 'general' }), emptyRun())
  assert.equal(ui.label, 'Failed · Redrawing figures')
  assert.equal(ui.fromStage, 'render')
})

test('an input failure shows its own message and what to do', () => {
  const message = 'This recording has no sound. Upload a recording with speech.'
  const view = failureView(
    failedSession({ current_stage: 'ingest', failure_message: message, failure_kind: 'input' }), emptyRun())
  assert.deepEqual(view, {
    message, kind: 'input', stage: 'ingest', headline: "Iris couldn't use this recording",
  })
  // From this run's error event too.
  const run = reduceEvents([ev('status', { status: 'processing' }), ev('stage_started', { stage: 'ingest' }),
    ev('error', { message, kind: 'input', stage: 'ingest' })])
  assert.equal(failureView(failedSession({}), run).kind, 'input')
  assert.equal(failureView(failedSession({}), run).message, message)
  assert.equal(run.stages.ingest.status, 'failed')
})

test('raw text from a crafted session or event is never shown', () => {
  // A session whose message isn't on the list (say, an old raw error) gets the generic message.
  const crafted = failedSession({ failure_message: SECRET as Session['failure_message'], failure_kind: 'input' })
  const view = failureView(crafted, emptyRun())
  assert.equal(view.message, GENERIC_FAILURE)
  assert.equal(view.kind, 'general') // an unknown message can't claim to be an input problem
  assert.ok(!shown(view).includes('SECRET') && !shown(view).includes('openai'))
  // An old session with the raw `error` field and nothing else.
  const old = { ...failedSession({}), error: SECRET } as Session
  assert.ok(!shown(failureView(old, emptyRun())).includes('SECRET'))
  assert.ok(!shown(uiState(old, emptyRun())).includes('SECRET'))
  // An error event carrying raw text: dropped for the generic message.
  const run = reduceEvents([ev('stage_started', { stage: 'write' }),
    ev('error', { message: SECRET, kind: 'input', stage: 'write' })])
  assert.deepEqual(run.failure, { message: GENERIC_FAILURE, kind: 'general', stage: 'write' })
  assert.ok(!shown(failureView(failedSession({}), run)).includes('SECRET'))
  assert.equal(knownFailureMessage(undefined), GENERIC_FAILURE)
  assert.equal(knownFailureMessage({ toString: () => SECRET }), GENERIC_FAILURE)
  assert.equal(knownFailureMessage('__proto__'), GENERIC_FAILURE)
})

test('a failure without a known step names none', () => {
  const view = failureView(failedSession({ current_stage: null, failure_message: GENERIC_FAILURE }), emptyRun())
  assert.equal(view.headline, "Iris couldn't finish the notes")
})
