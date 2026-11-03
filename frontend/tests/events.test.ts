import assert from 'node:assert/strict'
import test from 'node:test'
import { decodeSessionEvent } from '../src/api/eventMessage.ts'

const payload = {
  type: 'error', session_id: 's1', at: '2026-11-01T00:00:00Z',
  data: { message: 'Model unavailable' },
}

test('transport errors do not become application events', () => {
  assert.equal(decodeSessionEvent(new Event('error'), 's1'), null)
})

test('server errors and progress messages still reach the UI', () => {
  for (const type of ['error', 'stage_started', 'notes_updated']) {
    const message = { ...payload, type }
    assert.deepEqual(decodeSessionEvent(new MessageEvent(type, {
      data: JSON.stringify(message),
    }), 's1'), message)
  }
})

test('malformed, mismatched and foreign-session messages are ignored', () => {
  for (const data of [undefined, '', '{broken', 'null', '[]', JSON.stringify({ ...payload,
    session_id: 's2' }), JSON.stringify({ ...payload, type: 'status' }),
    JSON.stringify({ ...payload, data: null })]) {
    assert.equal(decodeSessionEvent(new MessageEvent('error', { data }), 's1'), null)
  }
})
