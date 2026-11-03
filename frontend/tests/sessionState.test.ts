import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent, Notes, Session } from '../src/api/client.ts'
import {
  emptyRun, eventKey, failureHeadline, figureList, formatClock, formatDuration, libraryState, provenance,
  reduceEvents, sessionLength, stageDetail, stageFromError, stageSeconds, transcriptSegments,
  uiState,
} from '../src/session/state.ts'

let tick = 0
function ev(type: IrisEvent['type'], data: Record<string, unknown> = {}): IrisEvent {
  tick += 1
  return { type, session_id: 's1', data, at: new Date(Date.UTC(2026, 10, 3, 12, 0, tick)).toISOString() }
}

const session: Session = {
  id: 's1', title: 'Algebra', source: 'upload', status: 'processing', current_stage: null,
  error: null, duration_s: null, created_at: '2026-11-03T12:00:00Z', updated_at: '2026-11-03T12:00:00Z',
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
  assert.equal(uiState(session, run).label, 'Processing · Transcribing')
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
  // A screenshot in the notes may be a fallback crop: no label without this run's events.
  assert.equal(provenance(figureList(emptyRun(), notes)[0]), undefined)
  const shot = reduceEvents([ev('visual_rendered', { id: 'v1', path: 'visuals/v1.jpg',
    render_as: 'screenshot', verified: true })])
  assert.equal(provenance(figureList(shot, notes)[0]), 'screenshot')
  assert.equal(provenance(fig), 'unverified')
  assert.equal(provenance({ id: 'v9', caption: '', rendered: false }), undefined)
})

test('a new run clears the previous run, and replays of its start do not', () => {
  const run1 = [ev('status', { status: 'processing' }), ev('stage_started', { stage: 'write' }),
    ev('error', { message: 'write failed: TimeoutError: slow' }), ev('status', { status: 'failed' })]
  const failed = reduceEvents(run1)
  assert.equal(failed.stages.write.status, 'failed')
  assert.equal(uiState({ ...session, status: 'failed' }, failed).fromStage, 'write')

  const start2 = ev('status', { status: 'processing' })
  const run2 = reduceEvents([...run1, start2, ev('stage_started', { stage: 'write' }), start2])
  assert.equal(run2.status, 'processing')
  assert.equal(run2.error, undefined)
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
    ev('stage_started', { stage: 'render' }), ev('error', { message: 'render failed: boom' })])
  assert.equal(run.stages.render.status, 'failed')
  assert.equal(run.stages.write.status, 'stopped')
  const unnamed = reduceEvents([ev('stage_started', { stage: 'align' }),
    ev('error', { message: 'KeyError: x' })])
  assert.equal(unnamed.stages.align.status, 'failed')
})

test('UI states cover recording, draft, stalled, finished and failed', () => {
  const draft = reduceEvents([ev('notes_updated', { final: false, completed_parts: 2,
    total_parts: 4 })])
  assert.equal(uiState(session, draft).kind, 'draft')
  assert.equal(uiState(session, draft).label, 'Draft · 2 of 4 sections')
  const final = reduceEvents([ev('notes_updated', { final: false, completed_parts: 4,
    total_parts: 4 }), ev('notes_updated', { final: true })])
  assert.equal(final.notes?.completed, 4)
  assert.equal(uiState(session, final).kind, 'processing')

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

test('library rows use the stored session only', () => {
  const failed = { ...session, status: 'failed' as const, current_stage: 'ingest' as const,
    error: 'ingest failed: NotImplementedError: browser live ingest' }
  assert.equal(libraryState(failed).label, 'Failed · Preparing the recording')
  assert.equal(libraryState({ ...failed, error: 'boom' }).fromStage, 'ingest')
  assert.equal(libraryState({ ...session, current_stage: 'render' }).label,
    'Processing · Redrawing figures')
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
  assert.equal(stageFromError('select failed: x'), 'select')
  assert.equal(stageFromError('bogus failed: x'), undefined)
  assert.equal(failureHeadline('write failed: x'), 'Writing notes failed')
  assert.equal(failureHeadline(null), 'Processing failed')
  const segs = [{ start: 0, end: 61.5, text: 'a' }]
  assert.equal(sessionLength(session, segs), 61.5)
  assert.equal(sessionLength(session, []), undefined)
  assert.equal(sessionLength({ ...session, duration_s: 90 }, segs), 90)
})
