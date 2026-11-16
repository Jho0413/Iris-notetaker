const assert = require('node:assert/strict')
const { test } = require('node:test')
const { REQUEST_TIMEOUT_MS } = require('../src/backend.cjs')
const { busyDetail, busyKeyRefusal, busyNow, quitDetail, recordingIds, stopRecordings } = require('../src/busy.cjs')

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
    { id: 'a', status: 'recording' },
    { id: 'b', status: 'processing' },
    { id: 'c', status: 'done' },
    { id: 'd', status: 'failed' },
  ]
  assert.deepEqual(busyNow(sessions, { capturing: true }).map((s) => s.id), ['a', 'b'])
  assert.deepEqual(busyNow(sessions, { capturing: false }).map((s) => s.id), ['b'])
  assert.deepEqual(busyNow([{ id: 'e', status: 'stopped' }], { capturing: true }), [])
})

test('no session list (the engine is down or answered oddly) means nothing busy', () => {
  for (const answer of [null, undefined, {}, 'oops']) assert.deepEqual(busyNow(answer, { capturing: true }), [])
  assert.deepEqual(busyNow([null, { status: 'recording' }], { capturing: true }), [{ status: 'recording' }])
})

test('quitting says what happens to each busy session', () => {
  assert.equal(
    quitDetail([session('Lecture 3')]),
    '“Lecture 3” is still recording. Quitting stops the recording and keeps what was recorded: make notes from it in the library.',
  )
  const making = { id: 'b', title: 'Standup', status: 'processing' }
  assert.equal(
    quitDetail([making]),
    '“Standup” is still making notes. Quitting stops it and keeps the finished steps: Try again in the library carries on.',
  )
  assert.equal(
    quitDetail([session('Lecture 3'), making]),
    '“Lecture 3” is still recording. Quitting stops the recording and keeps what was recorded: make notes from it in the library.' +
      '\n\n“Standup” is still making notes. Quitting stops it and keeps the finished steps: Try again in the library carries on.',
  )
})

test('quitting words several sessions together', () => {
  assert.equal(
    quitDetail([session('A'), session('')]),
    '“A”, “Untitled session” are still recording. Quitting stops the recordings and keeps what was recorded: make notes from them in the library.',
  )
  const making = ['C', 'D'].map((title) => ({ id: title, title, status: 'processing' }))
  assert.match(quitDetail(making), /^“C”, “D” are still making notes\. Quitting stops them and keeps/)
})

test('every session still recording is found to stop, whoever recorded it', () => {
  const sessions = [
    { id: 'a', status: 'recording' },
    { id: 'b', status: 'processing' },
    { id: 'c', status: 'stopped' },
    { id: 'd', status: 'recording' },
    { status: 'recording' },
    null,
  ]
  assert.deepEqual(recordingIds(sessions), ['a', 'd'])
  for (const answer of [null, undefined, {}, 'oops']) assert.deepEqual(recordingIds(answer), [])
})

function fakeBackend(sessions, answer = 200) {
  const calls = []
  return {
    calls,
    getJson: async (path, timeoutMs) => {
      calls.push(['GET', path, timeoutMs])
      return sessions
    },
    post: async (path) => {
      calls.push(['POST', path])
      return answer
    },
  }
}

test('stopping recordings reads the list, then stops each recording and waits for it', async () => {
  const backend = fakeBackend([
    { id: 'r 1', status: 'recording' },
    { id: 'b', status: 'processing' },
    { id: 'c', status: 'stopped' },
  ])
  const logged = []
  await stopRecordings(backend, 'quitting', (line) => logged.push(line))
  assert.deepEqual(backend.calls.map(([method, path]) => [method, path]), [
    ['GET', '/api/sessions'],
    ['POST', '/api/live/r%201/stop'],
  ])
  assert.ok(backend.calls[0][2] > REQUEST_TIMEOUT_MS, 'the list gets longer than the default: it adds up costs')
  assert.deepEqual(logged, ['[sessions] stopped recordings (quitting): r 1 200'])
})

test('an engine that doesn\'t answer a stop is logged as such', async () => {
  const logged = []
  await stopRecordings(fakeBackend([{ id: 'a', status: 'recording' }], 0), 'launch', (line) => logged.push(line))
  assert.deepEqual(logged, ['[sessions] stopped recordings (launch): a no answer'])
})

test('a session list that can\'t be read stops nothing, and says so', async () => {
  for (const answer of [null, {}, 'oops']) {
    const backend = fakeBackend(answer)
    const logged = []
    await stopRecordings(backend, 'quitting', (line) => logged.push(line))
    assert.deepEqual(backend.calls.map(([method]) => method), ['GET'])
    assert.deepEqual(logged, ["[sessions] couldn't read the sessions to stop recordings (quitting): none stopped"])
  }
})

test('with nothing recording, nothing is stopped or logged', async () => {
  const backend = fakeBackend([{ id: 'b', status: 'done' }])
  const logged = []
  await stopRecordings(backend, 'launch', (line) => logged.push(line))
  assert.equal(backend.calls.length, 1)
  assert.deepEqual(logged, [])
})
