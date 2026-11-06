// LiveCapture lifecycle with fake browser APIs (no real devices, recorder or network).
import assert from 'node:assert/strict'
import test from 'node:test'
import { CaptureError } from '../src/capture/captureErrors.ts'
import {
  LiveCapture,
  SHARED_AUDIO,
  type StartWorker,
  sendBeaconOrKeepalive,
  startTicker,
} from '../src/capture/liveCapture.ts'

class FakeTrack extends EventTarget {
  kind: string
  stopped = false
  constructor(kind: string) {
    super()
    this.kind = kind
  }
  stop() {
    this.stopped = true
  }
}

function stream(...tracks: FakeTrack[]) {
  return {
    getTracks: () => tracks,
    getAudioTracks: () => tracks.filter((t) => t.kind === 'audio'),
    getVideoTracks: () => tracks.filter((t) => t.kind === 'video'),
  }
}

class FakeRecorder extends EventTarget {
  static isTypeSupported = () => true
  static created: FakeRecorder[] = []
  state = 'inactive'
  ondataavailable: ((e: { data: Blob }) => void) | null = null
  onerror: (() => void) | null = null
  constructor() {
    super()
    FakeRecorder.created.push(this)
  }
  start() {
    this.state = 'recording'
    queueMicrotask(() => this.dispatchEvent(new Event('start')))
  }
  emit(text: string) {
    this.ondataavailable?.({ data: new Blob([text]) })
  }
  stop() {
    this.state = 'inactive'
    this.emit('last') // MediaRecorder delivers the final chunk before 'stop'
    queueMicrotask(() => this.dispatchEvent(new Event('stop')))
  }
}

interface Call {
  url: string
  method: string
  body?: unknown
}

function setup(overrides: Record<string, unknown> = {}) {
  FakeRecorder.created = []
  const calls: Call[] = []
  const beacons: { url: string; body: Blob }[] = []
  const display = new FakeTrack('video')
  const mic = new FakeTrack('audio')
  let onFrame: ((jpeg: Blob, at: number) => void) | undefined
  let failing = (_url: string) => false
  const env = {
    mediaDevices: {
      getDisplayMedia: async () => stream(display),
      getUserMedia: async () => stream(mic),
    },
    MediaRecorder: FakeRecorder,
    fetch: async (url: string, init?: RequestInit) => {
      if (failing(url)) throw new TypeError('Failed to fetch')
      calls.push({ url, method: init?.method ?? 'GET', body: init?.body })
      const status = url.includes('/audio') || url.includes('/frame') || init?.method === 'DELETE' ? 204 : 200
      return new Response(status === 204 ? null : JSON.stringify({ status: 'processing' }), { status })
    },
    sendBeacon: (url: string, body: Blob) => {
      beacons.push({ url, body })
      return true
    },
    page: new EventTarget(),
    mixAudio: () => ({ stream: stream(), close() {} }),
    captureFrames: (_track: unknown, cb: (jpeg: Blob, at: number) => void) => {
      onFrame = cb
      return () => {}
    },
    ...overrides,
  }
  return {
    env,
    calls,
    beacons,
    display,
    mic,
    frame: (at: number) => onFrame?.(new Blob(['jpeg']), at),
    failWhen: (f: (url: string) => boolean) => (failing = f),
    posted: (part: string) => calls.filter((c) => c.method === 'POST' && c.url.includes(part)),
  }
}

const fast = { drainTimeoutMs: 200, uploads: { baseDelayMs: 1, maxDelayMs: 5 } }

function capture(env: object, options = {}) {
  // The fake env stands in for the browser APIs; its shapes are only what LiveCapture uses.
  return new LiveCapture('s1', { ...fast, ...options }, env as never)
}

test('records, then stop uploads the last chunk and ends with the chunk count', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  const recorder = FakeRecorder.created[0]
  recorder.emit('a')
  recorder.emit('b')
  t.frame(performance.now())
  await live.stop()

  assert.deepEqual(
    t.posted('/audio').map((c) => c.url),
    ['/api/live/s1/audio?seq=0', '/api/live/s1/audio?seq=1', '/api/live/s1/audio?seq=2'],
  )
  assert.equal(t.posted('/frame').length, 1)
  const end = t.posted('/end')
  assert.equal(end.length, 1)
  assert.deepEqual(JSON.parse(end[0].body as string), { audio_chunks: 3, allow_gaps: false })
  assert.ok(t.display.stopped && t.mic.stopped)
  // Finished: leaving the page no longer ends anything, and frames are no longer queued.
  t.env.page.dispatchEvent(new Event('pagehide'))
  t.frame(performance.now())
  assert.equal(t.beacons.length, 0)
  assert.equal(t.posted('/frame').length, 1)
})

test('stop called twice ends once', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  const [a, b] = [live.stop(), live.stop()]
  assert.equal(a, b)
  await a
  assert.equal(t.posted('/end').length, 1)
})

test('a failed stop can be retried without losing what was recorded', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  FakeRecorder.created[0].emit('a')
  t.failWhen((url) => url.includes('/audio'))
  await assert.rejects(live.stop(), /haven't reached Iris/)
  assert.equal(t.posted('/end').length, 0)

  t.failWhen(() => false) // the backend is back
  await live.stop()
  assert.equal(t.posted('/audio').length, 2)
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 2, allow_gaps: false })
})

test('a failed end request can be retried', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  t.failWhen((url) => url.endsWith('/end'))
  await assert.rejects(live.stop(), /Iris isn't responding/)
  t.failWhen(() => false)
  await live.stop()
  assert.equal(t.posted('/end').length, 1)
})

test('screen sharing stopped from the browser calls onEnded once', async () => {
  const t = setup()
  const reasons: string[] = []
  const live = capture(t.env, { onEnded: (reason: string) => reasons.push(reason) })
  await live.start()
  t.display.dispatchEvent(new Event('ended'))
  assert.deepEqual(reasons, ['screen-share-ended'])
  const stopping = live.stop()
  t.mic.dispatchEvent(new Event('ended')) // ignored while stopping
  await stopping
  assert.deepEqual(reasons, ['screen-share-ended'])
})

test('without an onEnded handler, capture finishes itself when sharing stops', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  t.display.dispatchEvent(new Event('ended'))
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(t.posted('/end').length, 1)
})

test('leaving the page while recording ends the session with what arrived', async () => {
  const t = setup()
  await capture(t.env).start()
  const unload = new Event('beforeunload', { cancelable: true })
  t.env.page.dispatchEvent(unload)
  assert.equal(unload.defaultPrevented, true) // the browser asks "leave site?"
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(t.beacons.length, 1)
  assert.equal(t.beacons[0].url, '/api/live/s1/end')
  assert.deepEqual(JSON.parse(await t.beacons[0].body.text()), { allow_gaps: true })
  await tick()
  assert.equal(t.calls.filter((c) => c.method === 'DELETE').length, 0) // what was recorded is kept
})

test('screen sharing denied: the mic is not requested and the empty session is deleted', async () => {
  let micAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
      getUserMedia: async () => {
        micAsked = true
        return stream()
      },
    },
  })
  await assert.rejects(capture(t.env).start(), (err: unknown) =>
    err instanceof CaptureError && /Screen sharing was cancelled/.test(err.message),
  )
  assert.equal(micAsked, false)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('mic denied: the screen share is released and the session deleted', async () => {
  const display = new FakeTrack('video')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(display),
      getUserMedia: async () => {
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
    },
  })
  await assert.rejects(capture(t.env).start(), /Microphone access was blocked/)
  assert.equal(display.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('stop while the mic prompt is open cancels the start instead of recording', async () => {
  let grantMic: (s: unknown) => void = () => {}
  const mic = new FakeTrack('audio')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise((resolve) => (grantMic = resolve)),
    },
  })
  const live = capture(t.env)
  const starting = live.start()
  await new Promise((r) => setTimeout(r, 0)) // now waiting on the mic prompt
  const stopping = live.stop()
  grantMic(stream(mic)) // the user allows the mic after leaving the page
  await assert.rejects(starting, /cancelled/)
  await stopping
  assert.equal(FakeRecorder.created.length, 0)
  assert.ok(t.display.stopped && mic.stopped)
  assert.equal(t.posted('/end').length, 0)
  assert.equal(t.calls.filter((c) => c.method === 'DELETE').length, 1)
})

test('no WebM support: a clear error before asking for anything', async () => {
  class NoWebm extends FakeRecorder {
    static isTypeSupported = () => false
  }
  const t = setup({ MediaRecorder: NoWebm })
  await assert.rejects(capture(t.env).start(), /can't record WebM audio/)
})

test('if the backend still misses audio after every upload landed, it ends with gaps', async () => {
  const ends: unknown[] = []
  const t = setup()
  const env = {
    ...t.env,
    fetch: async (url: string, init?: RequestInit) => {
      if (url.endsWith('/end')) {
        const body = JSON.parse(init!.body as string)
        ends.push(body)
        if (!body.allow_gaps) {
          const detail = { message: '1 audio chunks are missing', missing_audio_chunks: [0] }
          return new Response(JSON.stringify({ detail }), { status: 409 })
        }
        return new Response('{}', { status: 200 })
      }
      return new Response(null, { status: 204 })
    },
  }
  const live = capture(env)
  await live.start()
  await live.stop()
  assert.deepEqual(ends, [
    { audio_chunks: 1, allow_gaps: false },
    { audio_chunks: 1, allow_gaps: true },
  ])
})

// --- Partial starts: whatever was acquired before a failure is released at once ---------------

/** Fails instead of hanging when `p` doesn't settle in time. */
function within<T>(p: Promise<T>, ms = 200): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`still pending after ${ms} ms`)), ms)
  })
  return Promise.race([p, timeout]).finally(() => clearTimeout(timer))
}

const tick = () => new Promise((r) => setTimeout(r, 0))

test('mic denied with tab audio shared: every screen track is stopped', async () => {
  const video = new FakeTrack('video')
  const tabAudio = new FakeTrack('audio')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(video, tabAudio),
      getUserMedia: async () => {
        throw new DOMException('Requested device not found', 'NotFoundError')
      },
    },
  })
  await assert.rejects(capture(t.env).start(), /No microphone was found/)
  assert.ok(video.stopped && tabAudio.stopped)
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('stop while the mic prompt stays open releases the screen share without waiting', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise(() => {}), // the user never answers the prompt
    },
  })
  const live = capture(t.env)
  const starting = live.start()
  await tick() // now waiting on the mic prompt
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  assert.equal(t.display.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('screen sharing stopped during the mic prompt fails the start and releases the mic too', async () => {
  let grantMic: (s: unknown) => void = () => {}
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise((resolve) => (grantMic = resolve)),
    },
  })
  const starting = capture(t.env).start()
  await tick()
  t.display.dispatchEvent(new Event('ended')) // "Stop sharing" in the browser's bar
  await assert.rejects(within(starting), /Screen sharing stopped before recording started/)
  grantMic(stream(t.mic)) // the user answers the prompt afterwards
  await tick()
  assert.equal(t.mic.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the recorder failing before it starts releases the screen and mic', async () => {
  class FailingRecorder extends FakeRecorder {
    start() {
      this.state = 'recording'
      queueMicrotask(() => {
        this.state = 'inactive'
        this.onerror?.()
      })
    }
  }
  const t = setup({ MediaRecorder: FailingRecorder })
  await assert.rejects(within(capture(t.env).start()), /could not start recording/)
  assert.ok(t.display.stopped && t.mic.stopped)
  await tick()
  assert.equal(t.posted('/audio').length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the recorder refusing the stream releases the screen, mic and audio mixer', async () => {
  let mixerClosed = false
  class RefusingRecorder extends FakeRecorder {
    constructor() {
      super()
      throw new DOMException('Unsupported stream', 'NotSupportedError')
    }
  }
  const tabAudio = new FakeTrack('audio')
  const t = setup({
    MediaRecorder: RefusingRecorder,
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display, tabAudio),
      getUserMedia: async () => stream(t.mic),
    },
    mixAudio: () => ({ stream: stream(), close: () => (mixerClosed = true) }),
  })
  await assert.rejects(capture(t.env).start(), CaptureError)
  assert.ok(t.display.stopped && tabAudio.stopped && t.mic.stopped && mixerClosed)
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('stop while the recorder is starting drops its late chunk and discards the session once', async () => {
  let fireStart = () => {}
  class SlowRecorder extends FakeRecorder {
    start() {
      this.state = 'recording'
      fireStart = () => this.dispatchEvent(new Event('start'))
    }
  }
  const t = setup({ MediaRecorder: SlowRecorder })
  const live = capture(t.env)
  const starting = live.start()
  await tick() // the recorder exists but hasn't reported 'start'
  assert.equal(FakeRecorder.created.length, 1)
  await within(live.stop())
  fireStart()
  await assert.rejects(within(starting), /cancelled/)
  await tick()
  assert.ok(t.display.stopped && t.mic.stopped)
  assert.equal(t.posted('/audio').length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('stop while the screen picker is open: a screen granted later is stopped, the mic never asked', async () => {
  let grantScreen: (s: unknown) => void = () => {}
  let micAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: () => new Promise((resolve) => (grantScreen = resolve)),
      getUserMedia: async () => {
        micAsked = true
        return stream(t.mic)
      },
    },
  })
  const live = capture(t.env)
  const starting = live.start()
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  grantScreen(stream(t.display))
  await tick()
  assert.equal(t.display.stopped, true)
  assert.equal(micAsked, false)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('closing the tab during a prompt discards the empty session', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise(() => {}),
    },
  })
  const starting = capture(t.env).start()
  await tick()
  t.env.page.dispatchEvent(new Event('pagehide'))
  await assert.rejects(within(starting), /cancelled/) // a page restored from cache can't record
  assert.equal(t.display.stopped, true)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
  assert.equal(t.beacons.length, 0) // nothing to end: it never recorded
})

test('frame capture failing to start leaves nothing half-started', async () => {
  const t = setup({
    captureFrames: () => {
      throw new Error('no canvas')
    },
  })
  const live = capture(t.env)
  await assert.rejects(live.start(), (err: unknown) => err instanceof Error && err.message === "Recording couldn't start. Try again.")
  assert.ok(t.display.stopped && t.mic.stopped)
  const unload = new Event('beforeunload', { cancelable: true })
  t.env.page.dispatchEvent(unload)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(unload.defaultPrevented, false)
  assert.equal(t.beacons.length, 0)
  // Nothing to finish: stop after a failed start doesn't end the deleted session.
  await live.stop()
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the recorder failing mid-recording ends only after its last chunk', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start()
  const recorder = FakeRecorder.created[0]
  recorder.emit('a')
  // As browsers do: inactive when 'error' fires, then the last chunk, then 'stop'.
  recorder.state = 'inactive'
  recorder.onerror?.()
  setTimeout(() => {
    recorder.emit('last')
    recorder.dispatchEvent(new Event('stop'))
  }, 10)
  await new Promise((r) => setTimeout(r, 50))
  const order = t.calls.filter((c) => c.method === 'POST').map((c) => c.url.replace('/api/live/s1/', ''))
  assert.deepEqual(order, ['audio?seq=0', 'audio?seq=1', 'end'])
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 2, allow_gaps: false })
  assert.equal(live.pendingUploads, 0)
})

test('shared audio is requested without voice processing; the mic keeps it', async () => {
  const asked: { display: unknown[]; mic: unknown[] } = { display: [], mic: [] }
  const tabAudio = new FakeTrack('audio')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async (c: unknown) => (asked.display.push(c), stream(t.display, tabAudio)),
      getUserMedia: async (c: unknown) => (asked.mic.push(c), stream(t.mic)),
    },
  })
  const live = capture(t.env)
  await live.start()
  assert.deepEqual(asked.display, [
    { video: true, audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } },
  ])
  assert.deepEqual(SHARED_AUDIO, { echoCancellation: false, noiseSuppression: false, autoGainControl: false })
  assert.deepEqual(asked.mic, [{ audio: true }])
  await live.stop()
})

for (const name of ['TypeError', 'NotSupportedError']) {
  test(`shared audio refused (${name}): the screen alone is asked for, and recording goes on with the mic`, async () => {
    const asked: unknown[] = []
    const t = setup({
      mediaDevices: {
        getDisplayMedia: async (c: { audio?: unknown }) => {
          asked.push(c)
          if (c.audio) throw name === 'TypeError' ? new TypeError('audio unsupported') : new DOMException('no audio', name)
          return stream(t.display)
        },
        getUserMedia: async () => stream(t.mic),
      },
    })
    const live = capture(t.env)
    await live.start()
    assert.deepEqual(asked, [{ video: true, audio: SHARED_AUDIO }, { video: true }])
    assert.equal(FakeRecorder.created.length, 1)
    await live.stop()
  })
}

test('a refused screen share is not retried without audio', async () => {
  const asked: unknown[] = []
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async (c: unknown) => {
        asked.push(c)
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
      getUserMedia: async () => stream(t.mic),
    },
  })
  await assert.rejects(capture(t.env).start(), CaptureError)
  assert.equal(asked.length, 1)
})

test('sendBeaconOrKeepalive: a beacon where allowed, else a keepalive POST', async () => {
  const body = new Blob(['{"allow_gaps":true}'], { type: 'application/json' })
  const fetched: { url: string; init: RequestInit }[] = []
  const fetchFn = async (url: string, init: RequestInit) => fetched.push({ url, init })

  const beacons: string[] = []
  assert.equal(sendBeaconOrKeepalive('/api/live/s1/end', body, (url) => (beacons.push(url), true), fetchFn), true)
  assert.deepEqual(beacons, ['/api/live/s1/end'])
  assert.equal(fetched.length, 0)

  const refuse = () => {
    throw new TypeError('Beacons are only supported over HTTP(S).')
  }
  assert.equal(sendBeaconOrKeepalive('/api/live/s1/end', body, refuse, fetchFn), true)
  assert.equal(fetched.length, 1)
  assert.equal(fetched[0].url, '/api/live/s1/end')
  assert.equal(fetched[0].init.method, 'POST')
  assert.equal(fetched[0].init.keepalive, true)
  assert.equal(fetched[0].init.body, body)
  assert.deepEqual(fetched[0].init.headers, { 'content-type': 'application/json' })

  // A failing fetch is swallowed: the page is going away.
  const failing = async () => {
    throw new TypeError('Failed to fetch')
  }
  assert.equal(sendBeaconOrKeepalive('/x', body, refuse, failing), true)
  await new Promise((r) => setTimeout(r, 0))
})

// --- The frame ticker (startTicker) ---------------------------------------------------------

/** A worker the test drives: `message()` is one tick from it, `error()` its error event. */
function fakeWorker(options: { throws?: boolean; errorsWhileStarting?: boolean } = {}) {
  const worker = { intervalMs: 0, terminated: 0, message: () => {}, error: (_e: Event) => {} }
  const start: StartWorker = (intervalMs, onMessage, onError) => {
    if (options.throws) throw new Error('Worker is not defined')
    worker.intervalMs = intervalMs
    worker.message = onMessage
    worker.error = onError
    if (options.errorsWhileStarting) onError(new Event('error'))
    return () => void worker.terminated++
  }
  return { worker, start }
}

function tickerTest(t: import('node:test').TestContext) {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const warn = t.mock.method(console, 'warn', () => {})
  const ticks = { count: 0 }
  return { ticks, tick: () => void ticks.count++, warn }
}

test('the ticker ticks on the worker, with no page timer', (t) => {
  const { ticks, tick, warn } = tickerTest(t)
  const { worker, start } = fakeWorker()
  const stop = startTicker(1000, tick, start)
  assert.equal(worker.intervalMs, 1000)
  worker.message()
  worker.message()
  t.mock.timers.tick(5000)
  assert.equal(ticks.count, 2)
  stop()
  assert.equal(worker.terminated, 1)
  worker.message()
  assert.equal(ticks.count, 2)
  assert.equal(warn.mock.callCount(), 0)
})

test('a worker that cannot be created falls back to a page timer', (t) => {
  const { ticks, tick, warn } = tickerTest(t)
  const stop = startTicker(1000, tick, fakeWorker({ throws: true }).start)
  t.mock.timers.tick(3000)
  assert.equal(ticks.count, 3)
  assert.equal(warn.mock.callCount(), 1)
  stop()
  t.mock.timers.tick(3000)
  assert.equal(ticks.count, 3)
})

test('a worker that errors after it was created (a CSP block) falls back to a page timer, once', (t) => {
  const { ticks, tick, warn } = tickerTest(t)
  const { worker, start } = fakeWorker()
  const stop = startTicker(1000, tick, start)
  worker.error(new Event('error'))
  assert.equal(worker.terminated, 1)
  t.mock.timers.tick(2000)
  assert.equal(ticks.count, 2)
  // The failed worker can't add ticks, and a second error doesn't start a second timer.
  worker.message()
  worker.error(new Event('error'))
  t.mock.timers.tick(1000)
  assert.equal(ticks.count, 3)
  assert.equal(warn.mock.callCount(), 1)
  stop()
  t.mock.timers.tick(3000)
  assert.equal(ticks.count, 3)
})

test('a worker that errors before it is returned is terminated and replaced', (t) => {
  const { ticks, tick } = tickerTest(t)
  const { worker, start } = fakeWorker({ errorsWhileStarting: true })
  const stop = startTicker(1000, tick, start)
  assert.equal(worker.terminated, 1)
  t.mock.timers.tick(2000)
  assert.equal(ticks.count, 2)
  stop()
})

test('a worker error after stop starts no timer', (t) => {
  const { ticks, tick, warn } = tickerTest(t)
  const { worker, start } = fakeWorker()
  const stop = startTicker(1000, tick, start)
  stop()
  worker.error(new Event('error'))
  t.mock.timers.tick(5000)
  assert.equal(ticks.count, 0)
  assert.equal(warn.mock.callCount(), 0)
})

test('the real worker, where there is no Worker (as in Node), falls back to a page timer', (t) => {
  const { ticks, tick, warn } = tickerTest(t)
  const stop = startTicker(1000, tick)
  t.mock.timers.tick(2000)
  assert.equal(ticks.count, 2)
  assert.equal(warn.mock.callCount(), 1)
  stop()
})
