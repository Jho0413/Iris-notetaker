import assert from 'node:assert/strict'
import test from 'node:test'
import { describeCaptureError, inDesktopApp, pickAudioMimeType } from '../src/capture/captureErrors.ts'
import { EndSessionError, endLiveSession } from '../src/capture/endSession.ts'
import { UploadQueue, isRetryable } from '../src/capture/uploadQueue.ts'

const blob = new Blob(['x'])
const fast = { baseDelayMs: 1, maxDelayMs: 2 }

test('failed uploads are retried until they land; rejected ones are reported, not retried', async () => {
  const attempts = new Map<string, number>()
  const queue = new UploadQueue(async (path) => {
    const n = (attempts.get(path) ?? 0) + 1
    attempts.set(path, n)
    if (path === 'audio?seq=0' && n === 1) throw new TypeError('Failed to fetch')
    if (path === 'audio?seq=1' && n < 3) return 503
    if (path === 'frame?t=1.000') return 415
    return 204
  }, fast)
  queue.add('audio', 'audio?seq=0', blob)
  queue.add('audio', 'audio?seq=1', blob)
  queue.add('frame', 'frame?t=1.000', blob)
  assert.equal(await queue.drain(1000), 0)
  assert.deepEqual(Object.fromEntries(attempts), {
    'audio?seq=0': 2,
    'audio?seq=1': 3,
    'frame?t=1.000': 1,
  })
  assert.deepEqual(queue.rejected, [{ kind: 'frame', path: 'frame?t=1.000', status: 415 }])
  assert.equal(queue.pending, 0)
})

test('a closed queue sends nothing more: neither what waits, nor retries, nor new items', async () => {
  const sent: string[] = []
  const queue = new UploadQueue(async (path) => {
    sent.push(path)
    return 503 // the first attempt fails, so it would be retried
  }, { ...fast, concurrency: 1 })
  queue.add('audio', 'audio?seq=0', blob)
  queue.add('audio', 'audio?seq=1', blob) // waits behind the first, which is in flight
  queue.close()
  queue.add('audio', 'audio?seq=2', blob)
  await new Promise((r) => setTimeout(r, 30))
  assert.deepEqual(sent, ['audio?seq=0'])
})

test('drain gives up after its timeout but keeps items, so a later drain still uploads them', async () => {
  let up = false
  const sent: string[] = []
  const queue = new UploadQueue(async (path) => {
    if (!up) throw new TypeError('Failed to fetch')
    sent.push(path)
    return 204
  }, fast)
  queue.add('audio', 'audio?seq=0', blob)
  assert.equal(await queue.drain(20), 1)
  assert.equal(queue.struggling, true)
  up = true
  assert.equal(await queue.drain(1000), 0)
  assert.deepEqual(sent, ['audio?seq=0'])
})

test('concurrency is bounded', async () => {
  let active = 0
  let most = 0
  const queue = new UploadQueue(async () => {
    most = Math.max(most, ++active)
    await new Promise((r) => setTimeout(r, 2))
    active--
    return 204
  }, { concurrency: 2 })
  for (let i = 0; i < 8; i++) queue.add('audio', `audio?seq=${i}`, blob)
  assert.equal(await queue.drain(1000), 0)
  assert.equal(most, 2)
})

test('while uploads fail, the oldest frames are dropped but audio and screen changes never are', async () => {
  let up = false
  const sent: string[] = []
  const queue = new UploadQueue(async (path) => {
    if (!up) throw new TypeError('Failed to fetch')
    sent.push(path)
    return 204
  }, { ...fast, concurrency: 1, maxQueuedFrames: 2 })
  queue.add('audio', 'audio?seq=0', blob)
  queue.add('screen', 'screen?t=0&on=true')
  for (let i = 0; i < 5; i++) queue.add('frame', `frame?t=${i}`, blob)
  queue.add('screen', 'screen?t=5&on=false')
  queue.add('audio', 'audio?seq=1', blob)
  up = true
  assert.equal(await queue.drain(1000), 0)
  assert.equal(queue.droppedFrames, 3)
  assert.deepEqual(sent.filter((p) => p.startsWith('audio')).sort(), ['audio?seq=0', 'audio?seq=1'])
  assert.deepEqual(sent.filter((p) => p.startsWith('frame')).sort(), ['frame?t=3', 'frame?t=4'])
  assert.deepEqual(sent.filter((p) => p.startsWith('screen')).sort(), ['screen?t=0&on=true', 'screen?t=5&on=false'])
})

test('retryable statuses', () => {
  assert.deepEqual([408, 429, 500, 503].map(isRetryable), [true, true, true, true])
  assert.deepEqual([400, 404, 409, 413, 415, 422].map(isRetryable), [false, false, false, false, false, false])
})

test('capture errors become actionable messages', () => {
  const denied = new DOMException('Permission denied', 'NotAllowedError')
  assert.match(describeCaptureError(denied, 'screen'), /cancelled or blocked/)
  assert.match(describeCaptureError(denied, 'microphone'), /Microphone access was blocked/)
  assert.match(describeCaptureError(new DOMException('', 'NotFoundError'), 'microphone'), /No microphone/)
  assert.match(describeCaptureError(new DOMException('', 'NotReadableError'), 'microphone'), /another app/i)
  // Anything else: our own words, never the browser's message.
  assert.equal(describeCaptureError('weird', 'screen'), "The screen couldn't be started. Try again.")
  assert.equal(
    describeCaptureError(new Error('SECRET internal detail'), 'microphone'),
    "The microphone couldn't be started. Try again.",
  )
})

test('in the desktop app, blocked capture points at System Settings for Iris, not a browser', () => {
  const denied = new DOMException('Permission denied', 'NotAllowedError')
  const screen = describeCaptureError(denied, 'screen', true)
  assert.match(screen, /allow Iris in System Settings → Privacy & Security → Screen & System Audio Recording/)
  assert.doesNotMatch(screen, /browser/)
  const mic = describeCaptureError(denied, 'microphone', true)
  assert.match(mic, /Allow Iris in System Settings → Privacy & Security → Microphone/)
  assert.doesNotMatch(mic, /browser|site/)
  // In a browser (and in Node, where there is no app:// page) the browser wording stays.
  assert.equal(inDesktopApp(), false)
  assert.match(describeCaptureError(denied, 'microphone'), /for this site in the browser/)
  assert.match(describeCaptureError(denied, 'screen', false), /for your browser/)
})

test("in the desktop app, a cancelled picker says so: Electron refuses it as AbortError", () => {
  const refused = new DOMException('Invalid capture constraints', 'AbortError')
  assert.equal(
    describeCaptureError(refused, 'screen', true),
    describeCaptureError(new DOMException('', 'NotAllowedError'), 'screen', true),
  )
  // Elsewhere an AbortError is still an interruption.
  assert.match(describeCaptureError(refused, 'screen', false), /interrupted/)
  assert.match(describeCaptureError(refused, 'microphone', true), /interrupted/)
})

test('audio format: WebM/Opus preferred, nothing if WebM is unsupported', () => {
  assert.equal(pickAudioMimeType(() => true), 'audio/webm;codecs=opus')
  assert.equal(pickAudioMimeType((t) => t === 'audio/webm'), 'audio/webm')
  assert.equal(pickAudioMimeType((t) => t === 'audio/mp4'), null)
})

function respond(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

test('ending sends the chunk count and succeeds', async () => {
  const calls: { url: string; init?: RequestInit }[] = []
  await endLiveSession('s1', { audioChunks: 7, allowGaps: false }, async (url, init) => {
    calls.push({ url, init })
    return respond(200, { status: 'processing' })
  })
  assert.equal(calls[0].url, '/api/live/s1/end')
  assert.deepEqual(JSON.parse(calls[0].init!.body as string), { audio_chunks: 7, allow_gaps: false })
})

test('ending with missing audio fails so it can be retried', async () => {
  const missing = { detail: { message: '1 audio chunks are missing', missing_audio_chunks: [3] } }
  await assert.rejects(
    endLiveSession('s1', { audioChunks: 7, allowGaps: false }, async () => respond(409, missing)),
    (err: unknown) =>
      err instanceof EndSessionError &&
      err.message === "Iris hasn't received all the audio yet. Try again in a moment." &&
      !err.message.includes('chunks are missing'), // the backend's text is never shown
  )
})

test('an end request the backend refuses gives our own message, with no status code', async () => {
  await assert.rejects(
    endLiveSession('s1', { audioChunks: 1, allowGaps: false }, async () => respond(500, { detail: 'SECRET' })),
    (err: unknown) =>
      err instanceof EndSessionError &&
      err.message === "Iris couldn't finish the session. Try again in a moment." &&
      !/500|SECRET|HTTP/.test(err.message),
  )
})

test('ending a session that already ended (lost response) counts as success', async () => {
  await endLiveSession('s1', { audioChunks: 1, allowGaps: false }, async (url) =>
    url.endsWith('/end') ? respond(409, { detail: 'session is not recording' }) : respond(200, { status: 'processing' }),
  )
  await assert.rejects(
    endLiveSession('s1', { audioChunks: 1, allowGaps: false }, async (url) =>
      url.endsWith('/end') ? respond(409, { detail: 'session is not recording' }) : respond(200, { status: 'recording' }),
    ),
    EndSessionError,
  )
})

test('ending while the backend is down fails with a clear message', async () => {
  await assert.rejects(
    endLiveSession('s1', { audioChunks: 1, allowGaps: false }, async () => {
      throw new TypeError('Failed to fetch')
    }),
    (err: unknown) =>
      err instanceof EndSessionError &&
      err.message === "Iris isn't responding, so the session couldn't be finished. Try again in a moment." &&
      !/make dev|backend/.test(err.message), // no developer instructions
  )
})
