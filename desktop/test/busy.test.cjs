const assert = require('node:assert/strict')
const { test } = require('node:test')
const { busyDetail, busyKeyRefusal, busyNow } = require('../src/busy.cjs')

const session = (title) => ({ id: title, title, status: 'recording' })

test('busyDetail names up to three sessions', () => {
  assert.equal(
    busyDetail([session('Lecture 3')]),
    '“Lecture 3” is still recording or making notes. What is recorded so far is kept: resume it from the library later.',
  )
  assert.match(busyDetail([session('A'), session('')]), /^“A”, “Untitled session” are still recording/)
  assert.match(busyDetail(['A', 'B', 'C', 'D'].map(session)), /^“A”, “B”, “C” and others are still/)
})

test('changing the key while a session is busy is refused, with OK only', () => {
  const dialog = busyKeyRefusal([session('Lecture 3')], 'change')
  assert.deepEqual(dialog.buttons, ['OK'])
  assert.equal(dialog.defaultId, 0)
  assert.equal(dialog.message, 'Iris is still busy.')
  assert.equal(dialog.detail, '“Lecture 3” is still recording or making notes. Change the key once it has finished.')
})

test('adding a key while a session is busy is refused too', () => {
  assert.equal(busyKeyRefusal([session('Lecture 3')], 'add').detail, '“Lecture 3” is still recording or making notes. Add the key once it has finished.')
})

test('removing the key while sessions are busy is refused, worded for several', () => {
  const dialog = busyKeyRefusal([session('A'), session('B')], 'remove')
  assert.deepEqual(dialog.buttons, ['OK'])
  assert.equal(dialog.detail, '“A”, “B” are still recording or making notes. Remove the key once they have finished.')
})

test('a recording counts as busy only while the Live page captures it', () => {
  const sessions = [
    { id: 'a', status: 'recording' }, // left by an earlier quit, or the one being captured now
    { id: 'b', status: 'processing' },
    { id: 'c', status: 'done' },
    { id: 'd', status: 'failed' },
  ]
  assert.deepEqual(busyNow(sessions, { capturing: true }).map((s) => s.id), ['a', 'b'])
  // Nothing capturing (e.g. a start cancelled in the picker): a leftover recording doesn't block.
  assert.deepEqual(busyNow(sessions, { capturing: false }).map((s) => s.id), ['b'])
})

test('no session list (the engine is down or answered oddly) means nothing busy', () => {
  for (const answer of [null, undefined, {}, 'oops']) assert.deepEqual(busyNow(answer, { capturing: true }), [])
  assert.deepEqual(busyNow([null, { status: 'recording' }], { capturing: true }), [{ status: 'recording' }])
})
