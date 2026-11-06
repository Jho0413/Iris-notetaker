const assert = require('node:assert/strict')
const { test } = require('node:test')
const { busyDetail, busyKeyRefusal } = require('../src/busy.cjs')

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
