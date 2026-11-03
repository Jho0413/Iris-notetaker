import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent } from '../src/api/client.ts'
import {
  BATCH_MS, connectSessionStream, QUIET_AFTER_MS, RETRY_MS, type StreamStatus,
} from '../src/session/stream.ts'

/** An EventSource the test drives: dispatches real MessageEvents like the browser does. */
class FakeSource extends EventTarget {
  readyState = 0
  closed = false
  onopen: ((e: Event) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  readonly url: string
  constructor(url: string) {
    super()
    this.url = url
  }
  close() {
    this.closed = true
    this.readyState = 2
  }
  send(type: IrisEvent['type'], data: Record<string, unknown>, at: string) {
    const message = { type, session_id: 's1', data, at }
    const event = new MessageEvent(type, { data: JSON.stringify(message) })
    this.dispatchEvent(event)
    // Like EventSource, a server "error" event also reaches onerror.
    if (type === 'error') this.onerror?.(event)
  }
  transportError(closed: boolean) {
    this.readyState = closed ? 2 : 0
    this.onerror?.(new Event('error'))
  }
}

/** Manual clock: timers run only when the test advances time. */
function harness() {
  let now = 0
  let nextId = 1
  const timers = new Map<number, { at: number; fn: () => void }>()
  const sources: FakeSource[] = []
  const batches: IrisEvent[][] = []
  const statuses: StreamStatus[] = []
  const quiet: boolean[] = []
  const stop = connectSessionStream(
    's1',
    {
      onEvents: (e) => batches.push(e),
      onStatus: (s) => statuses.push(s),
      onQuiet: (q) => quiet.push(q),
    },
    {
      createSource: (url) => {
        const s = new FakeSource(url)
        sources.push(s)
        return s
      },
      setTimeout: (fn, ms) => {
        timers.set(nextId, { at: now + ms, fn })
        return nextId++
      },
      clearTimeout: (id) => {
        if (id !== undefined) timers.delete(id)
      },
    },
  )
  function advance(ms: number) {
    const until = now + ms
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0]
      if (!due) break
      timers.delete(due[0])
      now = due[1].at
      due[1].fn()
    }
    now = until
  }
  return { sources, batches, statuses, quiet, advance, stop, latest: () => sources[sources.length - 1] }
}

test('events are batched, and replays after a reconnect are dropped', () => {
  const h = harness()
  assert.equal(h.latest().url, '/api/sessions/s1/events')
  h.latest().send('status', { status: 'processing' }, '2026-11-03T12:00:00Z')
  h.latest().send('stage_started', { stage: 'ingest' }, '2026-11-03T12:00:01Z')
  assert.equal(h.batches.length, 0)
  h.advance(BATCH_MS)
  assert.deepEqual(h.batches.map((b) => b.map((e) => e.type)), [['status', 'stage_started']])

  // The browser reconnects by itself and the backend replays its history.
  h.latest().transportError(false)
  assert.equal(h.statuses.at(-1), 'reconnecting')
  h.latest().send('status', { status: 'processing' }, '2026-11-03T12:00:00Z')
  h.latest().send('stage_started', { stage: 'ingest' }, '2026-11-03T12:00:01Z')
  h.latest().send('stage_finished', { stage: 'ingest' }, '2026-11-03T12:00:02Z')
  h.advance(BATCH_MS)
  assert.deepEqual(h.batches.at(-1)?.map((e) => e.type), ['stage_finished'])
  assert.equal(h.statuses.at(-1), 'open')
})

test('a connection that replays nothing turns quiet; an event clears it', () => {
  const h = harness()
  h.advance(QUIET_AFTER_MS - 1)
  assert.deepEqual(h.quiet, [])
  h.advance(1)
  assert.deepEqual(h.quiet, [true])
  h.latest().send('status', { status: 'done' }, '2026-11-03T12:00:00Z')
  assert.equal(h.quiet.at(-1), false)
})

test('after the browser gives up, reconnects back off: 1, 2, 5, then 10 seconds', () => {
  const h = harness()
  const delays: number[] = []
  for (let i = 0; i < 5; i++) {
    const before = h.sources.length
    h.latest().transportError(true)
    assert.equal(h.sources[before - 1].closed, true)
    let waited = 0
    while (h.sources.length === before) {
      h.advance(500)
      waited += 500
    }
    delays.push(waited)
  }
  assert.deepEqual(delays, [...RETRY_MS, RETRY_MS.at(-1)])

  // An event resets the backoff.
  h.latest().send('status', { status: 'done' }, '2026-11-03T12:00:00Z')
  const before = h.sources.length
  h.latest().transportError(true)
  h.advance(RETRY_MS[0])
  assert.equal(h.sources.length, before + 1)
})

test('server error events are progress, not transport failures', () => {
  const h = harness()
  h.latest().send('error', { message: 'write failed: boom' }, '2026-11-03T12:00:00Z')
  h.advance(BATCH_MS)
  assert.deepEqual(h.batches.at(-1)?.map((e) => e.type), ['error'])
  assert.notEqual(h.statuses.at(-1), 'reconnecting')
  assert.equal(h.sources.length, 1)
})

test('stopping closes the stream and cancels pending work', () => {
  const h = harness()
  h.latest().send('status', { status: 'processing' }, '2026-11-03T12:00:00Z')
  h.stop()
  assert.equal(h.latest().closed, true)
  h.advance(60_000)
  assert.equal(h.batches.length, 0)
  assert.deepEqual(h.quiet, [false])
})
