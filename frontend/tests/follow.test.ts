import assert from 'node:assert/strict'
import test from 'node:test'
import type { IrisEvent } from '../src/api/client.ts'
import { followScrollTop, isAtBottom } from '../src/session/follow.ts'
import { reduceEvents, transcriptSegments } from '../src/session/state.ts'

let tick = 0
function ev(type: IrisEvent['type'], data: Record<string, unknown> = {}): IrisEvent {
  tick += 1
  return { type, session_id: 's1', data, at: new Date(Date.UTC(2026, 10, 3, 12, 0, tick)).toISOString() }
}

test('the live transcript shows transcript_chunk lines in time order, once each', () => {
  const chunks = [
    ev('status', { status: 'recording' }),
    ev('transcript_chunk', { chunk: 1, segments: [{ start: 12, end: 20, text: ' then the chain rule' }] }),
    ev('transcript_chunk', { chunk: 0, segments: [{ start: 0, end: 12, text: 'First, limits.' }] }),
  ]
  const run = reduceEvents([...chunks, ...chunks]) // a reconnect replays history
  assert.deepEqual(transcriptSegments(run, null).map((s) => s.text.trim()), ['First, limits.', 'then the chain rule'])
})

test('a reader at the bottom follows new lines', () => {
  // 600px of lines in a 320px box, scrolled to the end (within the slack counts too).
  assert.ok(isAtBottom({ scrollHeight: 600, scrollTop: 280, clientHeight: 320 }))
  assert.ok(isAtBottom({ scrollHeight: 600, scrollTop: 260, clientHeight: 320 }))
  // A new line makes the list taller: the box moves to the new bottom.
  assert.equal(followScrollTop(true, { scrollHeight: 640, scrollTop: 280, clientHeight: 320 }), 320)
  // Fewer lines than fit: nothing to scroll.
  assert.ok(isAtBottom({ scrollHeight: 200, scrollTop: 0, clientHeight: 320 }))
  assert.equal(followScrollTop(true, { scrollHeight: 200, scrollTop: 0, clientHeight: 320 }), 0)
})

test('a reader scrolled up is never moved by new lines', () => {
  const reading = { scrollHeight: 600, scrollTop: 100, clientHeight: 320 }
  assert.equal(isAtBottom(reading), false)
  assert.equal(followScrollTop(false, { ...reading, scrollHeight: 640 }), null)
})
