import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent } from '../src/api/client.ts'
import { draftRefreshKey, fetchDraft } from '../src/session/draft.ts'
import { emptyRun, reduceEvents } from '../src/session/state.ts'

let tick = 0
function ev(type: IrisEvent['type'], data: Record<string, unknown> = {}): IrisEvent {
  tick += 1
  return { type, session_id: 's1', data, at: new Date(Date.UTC(2026, 10, 3, 12, 0, tick)).toISOString() }
}

const draft = { session_type: 'lecture', title: 'Algebra', markdown: '## Groups\n\n![Cayley table](visuals/v1.source.jpg)' }

test('the draft is fetched from the draft endpoint', async () => {
  const urls: string[] = []
  const ok = async (url: string | URL | Request) => {
    urls.push(String(url))
    return new Response(JSON.stringify(draft), { status: 200 })
  }
  assert.deepEqual(await fetchDraft('s 1', ok as typeof fetch), draft)
  assert.deepEqual(urls, ['/api/sessions/s%201/notes/draft'])
})

test('no draft gives null, so the page clears what it shows', async () => {
  // None yet, or dropped by the backend because the run is selecting figures again.
  const missing = async () => new Response('{"detail":"no draft notes"}', { status: 404 })
  assert.equal(await fetchDraft('s1', missing as typeof fetch), null)
})

test('an unknown answer gives undefined, so the page keeps what it shows', async () => {
  const offline = async () => {
    throw new TypeError('Failed to fetch')
  }
  assert.equal(await fetchDraft('s1', offline as typeof fetch), undefined)
  const failing = async () => new Response('{"detail":"boom"}', { status: 500 })
  assert.equal(await fetchDraft('s1', failing as typeof fetch), undefined)
  const garbled = async () => new Response('not json', { status: 200 })
  assert.equal(await fetchDraft('s1', garbled as typeof fetch), undefined)
})

test('the draft is refetched on every notes update while recording or processing', () => {
  // Fetched once on load, before any event.
  assert.equal(draftRefreshKey('recording', emptyRun()), 0)
  assert.equal(draftRefreshKey('processing', emptyRun()), 0)
  // Each live update (a section frozen, or the tail rewritten with the same count) changes the key.
  const first = ev('notes_updated', { final: false, completed_parts: 0, total_parts: 1, live: true })
  const rewrite = ev('notes_updated', { final: false, completed_parts: 0, total_parts: 1, live: true })
  const a = draftRefreshKey('recording', reduceEvents([first]))
  const b = draftRefreshKey('recording', reduceEvents([first, rewrite]))
  assert.ok(a && b && a !== b && b > a)
  // A replayed event changes nothing, so a reconnect doesn't refetch.
  assert.equal(draftRefreshKey('recording', reduceEvents([first, rewrite, first, rewrite])), b)
})

test('the draft is refetched when the run selects figures again, so a dropped draft clears', () => {
  // After stop: the live draft is shown, then the backend drops it before selecting again.
  const live = ev('notes_updated', { final: false, completed_parts: 1, total_parts: 2, live: true })
  const processing = ev('status', { status: 'processing' })
  const ingest = [ev('stage_started', { stage: 'ingest' }), ev('stage_finished', { stage: 'ingest' })]
  const select = ev('stage_started', { stage: 'select' })
  const figure = ev('visual_selected', { id: 'v000001', caption: 'x' })
  const selected = ev('stage_finished', { stage: 'select' })
  const section = ev('notes_updated', { final: false, completed_parts: 1, total_parts: 3 })

  const before = draftRefreshKey('processing', reduceEvents([live, processing, ...ingest]))
  const after = draftRefreshKey('processing', reduceEvents([live, processing, ...ingest, select]))
  assert.ok(before !== null && after !== null && after > before)
  // Figure events and the end of select don't refetch again.
  const busy = reduceEvents([live, processing, ...ingest, select, figure, selected])
  assert.equal(draftRefreshKey('processing', busy), after)
  // The write stage's first section is newer still.
  const written = reduceEvents([live, processing, ...ingest, select, figure, selected, section])
  assert.ok((draftRefreshKey('processing', written) ?? 0) > (after ?? 0))
})

test('no draft is wanted before recording, once finished or after the final notes', () => {
  assert.equal(draftRefreshKey(undefined, emptyRun()), null)
  assert.equal(draftRefreshKey('created', emptyRun()), null)
  assert.equal(draftRefreshKey('done', emptyRun()), null)
  assert.equal(draftRefreshKey('failed', emptyRun()), null)
  const final = reduceEvents([ev('notes_updated', { final: false, completed_parts: 2, total_parts: 2 }),
    ev('notes_updated', { final: true })])
  assert.equal(draftRefreshKey('processing', final), null)
})
