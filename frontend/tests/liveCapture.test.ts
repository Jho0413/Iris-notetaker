import assert from 'node:assert/strict'
import test from 'node:test'
import { CaptureError } from '../src/capture/captureErrors.ts'
import {
  LiveCapture,
  SHARED_AUDIO,
  clockStart,
  macSoundRefused,
  pausedClock,
  recordedMs,
  type StartWorker,
  sendBeaconOrKeepalive,
  startTicker,
} from '../src/capture/liveCapture.ts'

class FakeTrack extends EventTarget {
  kind: string
  stopped = false
  readyState: 'live' | 'ended' = 'live'
  constructor(kind: string) {
    super()
    this.kind = kind
  }
  stop() {
    this.stopped = true
    this.readyState = 'ended'
  }
  end() {
    this.readyState = 'ended'
    this.dispatchEvent(new Event('ended'))
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
  requestData() {
    this.emit('flushed')
  }
  pause() {
    this.state = 'paused'
  }
  resume() {
    this.state = 'recording'
  }
  stop() {
    this.state = 'inactive'
    this.emit('last')
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
  sessionsMade = 0
  const calls: Call[] = []
  const beacons: { url: string; body: Blob }[] = []
  const display = new FakeTrack('video')
  const mic = new FakeTrack('audio')
  let onFrame: ((jpeg: Blob, at: number) => void) | undefined
  let failing = (_url: string) => false
  const mixed: FakeMixer[] = []
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
    macSound: false,
    mixAudio: () => {
      const mixer = fakeMixer()
      mixed.push(mixer)
      return mixer
    },
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
    mixed,
    frame: (at: number) => onFrame?.(new Blob(['jpeg']), at),
    failWhen: (f: (url: string) => boolean) => (failing = f),
    posted: (part: string) => calls.filter((c) => c.method === 'POST' && c.url.includes(part)),
  }
}

interface FakeMixer {
  stream: ReturnType<typeof stream>
  live: ReturnType<typeof stream>[]
  closed: boolean
  add(s: ReturnType<typeof stream>): () => void
  close(): void
}

function fakeMixer(): FakeMixer {
  const mixer: FakeMixer = {
    stream: stream(),
    live: [],
    closed: false,
    add(s) {
      mixer.live.push(s)
      return () => (mixer.live = mixer.live.filter((x) => x !== s))
    },
    close() {
      mixer.closed = true
    },
  }
  return mixer
}

const fast = { drainTimeoutMs: 200, uploads: { baseDelayMs: 1, maxDelayMs: 5 } }

let sessionsMade = 0

function capture(env: object, options = {}) {
  const createSession = async () => {
    sessionsMade++
    return 's1'
  }
  return new LiveCapture(createSession, { ...fast, ...options }, env as never)
}

function fakeClock(tc: import('node:test').TestContext) {
  let ms = 0
  tc.mock.method(performance, 'now', () => ms)
  return { now: () => ms, advance: (by: number) => void (ms += by) }
}

test('records, then stop uploads the last chunk and ends with the chunk count', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
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
  t.env.page.dispatchEvent(new Event('pagehide'))
  t.frame(performance.now())
  assert.equal(t.beacons.length, 0)
  assert.equal(t.posted('/frame').length, 1)
})

test('stop called twice ends once', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  const [a, b] = [live.stop(), live.stop()]
  assert.equal(a, b)
  await a
  assert.equal(t.posted('/end').length, 1)
})

test('a failed stop can be retried without losing what was recorded', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  FakeRecorder.created[0].emit('a')
  t.failWhen((url) => url.includes('/audio'))
  await assert.rejects(live.stop(), /haven't reached Iris/)
  assert.equal(t.posted('/end').length, 0)

  t.failWhen(() => false)
  await live.stop()
  assert.equal(t.posted('/audio').length, 2)
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 2, allow_gaps: false })
})

test('a failed end request can be retried', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  t.failWhen((url) => url.endsWith('/end'))
  await assert.rejects(live.stop(), /Iris isn't responding/)
  t.failWhen(() => false)
  await live.stop()
  assert.equal(t.posted('/end').length, 1)
})

test('screen sharing stopped from the browser turns the screen off; the recording carries on', async () => {
  const t = setup()
  const reasons: string[] = []
  let screenEnded = 0
  const live = capture(t.env, { onEnded: (reason: string) => reasons.push(reason), onScreenEnded: () => screenEnded++ })
  await live.start('screen')
  t.frame(performance.now())
  t.display.end()
  assert.equal(screenEnded, 1)
  assert.deepEqual(reasons, [], 'only Stop ends a recording')
  assert.ok(!t.mic.stopped, 'the audio carries on')
  t.frame(performance.now())
  FakeRecorder.created[0].emit('a')
  await tick()
  assert.deepEqual(frameShares(t), [1])
  const stopping = live.stop()
  t.mic.dispatchEvent(new Event('ended'))
  await stopping
  assert.deepEqual(reasons, [])
  assert.equal(t.posted('/end').length, 1)
})

test('without an onEnded handler, capture finishes itself when the mic goes away', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  t.mic.end()
  await until(() => t.posted('/end').length === 1)
})

test('leaving the page while recording stops the session without making notes', async () => {
  const t = setup()
  await capture(t.env).start('screen')
  const unload = new Event('beforeunload', { cancelable: true })
  t.env.page.dispatchEvent(unload)
  assert.equal(unload.defaultPrevented, true)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(t.beacons.length, 1)
  assert.equal(t.beacons[0].url, '/api/live/s1/stop')
  assert.equal(t.beacons[0].body.type, 'application/json')
  assert.deepEqual(JSON.parse(await t.beacons[0].body.text()), {})
  await tick()
  assert.equal(t.calls.filter((c) => c.method === 'DELETE').length, 0)
  assert.equal(t.posted('/end').length, 0)
})

test('leaving the page after Make notes, while its upload is still finishing, makes the notes', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  FakeRecorder.created[0].emit('a')
  t.failWhen((url) => url.includes('/audio'))
  const making = live.stop()
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(t.beacons.length, 1)
  assert.equal(t.beacons[0].url, '/api/live/s1/end')
  assert.deepEqual(JSON.parse(await t.beacons[0].body.text()), { allow_gaps: true })
  await assert.rejects(making)
  t.failWhen(() => false)
  await live.stop()
})

test('leaving the page after Make notes failed still makes the notes', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  t.failWhen((url) => url.endsWith('/end'))
  await assert.rejects(live.stop(), /Iris isn't responding/)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(t.beacons.map((b) => b.url), ['/api/live/s1/end'])
})

test('keep (leaving the Live page) uploads the last chunk, then stops the session without notes', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  FakeRecorder.created[0].emit('a')
  await live.keep()

  assert.deepEqual(
    t.posted('/audio').map((c) => c.url),
    ['/api/live/s1/audio?seq=0', '/api/live/s1/audio?seq=1'],
  )
  const stop = t.posted('/stop')
  assert.equal(stop.length, 1)
  assert.deepEqual(JSON.parse(stop[0].body as string), {})
  assert.equal(t.posted('/end').length, 0)
  assert.equal(t.calls.filter((c) => c.method === 'DELETE').length, 0)
  assert.ok(t.display.stopped && t.mic.stopped)
  const unload = new Event('beforeunload', { cancelable: true })
  t.env.page.dispatchEvent(unload)
  assert.equal(unload.defaultPrevented, false)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(t.beacons.length, 0)
})

test('keep while stopped uploads what the recorder held, then stops the session', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  await live.keep()
  assert.equal(t.posted('/audio').length, 2)
  assert.equal(t.posted('/stop').length, 1)
  assert.equal(t.posted('/end').length, 0)
})

test('the tab closed while a keep is still uploading stops the session; it never makes notes', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  FakeRecorder.created[0].emit('a')
  t.failWhen((url) => url.includes('/audio'))
  const keeping = live.keep()
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(t.beacons.map((b) => b.url), ['/api/live/s1/stop'])
  assert.deepEqual(JSON.parse(await t.beacons[0].body.text()), {})
  await assert.rejects(keeping, /haven't reached Iris/)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(t.beacons.map((b) => b.url), ['/api/live/s1/stop', '/api/live/s1/stop'])

  t.failWhen(() => false)
  await live.keep()
  assert.equal(t.posted('/audio').length, 2)
  assert.equal(t.posted('/stop').length, 1)
  assert.equal(t.posted('/end').length, 0)
})

test('a failed keep request can be retried and still keeps', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  t.failWhen((url) => url.endsWith('/stop'))
  await assert.rejects(live.keep(), /Iris isn't responding, so the recording couldn't be stopped/)
  t.failWhen(() => false)
  await live.keep()
  assert.equal(t.posted('/stop').length, 1)
  assert.equal(t.posted('/end').length, 0)
})

function stopAnswering(status: number) {
  const t = setup()
  const fetchFn = t.env.fetch
  return capture({
    ...t.env,
    fetch: async (url: string, init?: RequestInit) =>
      url.endsWith('/stop') ? new Response(null, { status }) : fetchFn(url, init),
  })
}

test('keep: a session no longer recording, or gone, counts as kept; any other refusal is an error', async () => {
  for (const [status, ok] of [
    [409, true],
    [404, true],
    [500, false],
  ] as const) {
    const live = stopAnswering(status)
    await live.start('audio')
    if (ok) await live.keep()
    else await assert.rejects(live.keep(), /Iris couldn't stop the recording/)
  }
})

test("keep: a gateway error (only the engine is down) says Iris isn't responding", async () => {
  for (const status of [502, 503]) {
    const live = stopAnswering(status)
    await live.start('audio')
    await assert.rejects(live.keep(), /Iris isn't responding, so the recording couldn't be stopped/)
  }
})

test('keep after Make notes was chosen joins it, and retries the notes if they failed', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  const making = live.stop()
  assert.equal(live.keep(), making)
  await making
  assert.equal(t.posted('/end').length, 1)
  assert.equal(t.posted('/stop').length, 0)

  const u = setup()
  const failed = capture(u.env)
  await failed.start('audio')
  u.failWhen((url) => url.endsWith('/end'))
  await assert.rejects(failed.stop())
  u.failWhen(() => false)
  await failed.keep()
  assert.equal(u.posted('/end').length, 1)
  assert.equal(u.posted('/stop').length, 0)
})

test('Make notes after a keep makes them once the keep has settled', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  const keeping = live.keep()
  const making = live.stop()
  assert.notEqual(making, keeping)
  await making
  assert.deepEqual(
    t.calls.filter((c) => c.url.endsWith('/stop') || c.url.endsWith('/end')).map((c) => c.url),
    ['/api/live/s1/stop', '/api/live/s1/end'],
  )
  assert.equal(live.keep(), making)
})

test('the tab closed while Make notes waits behind a keep still in flight makes the notes', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  FakeRecorder.created[0].emit('a')
  t.failWhen((url) => url.includes('/audio'))
  const keeping = live.keep()
  const making = live.stop()
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(t.beacons.map((b) => b.url), ['/api/live/s1/end'])
  assert.deepEqual(JSON.parse(await t.beacons[0].body.text()), { allow_gaps: true })
  t.failWhen(() => false)
  await keeping.catch(() => {})
  await making
})

test('screen sharing denied after the mic: the mic is released and no session is made', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
      getUserMedia: async () => stream(t.mic),
    },
  })
  await assert.rejects(capture(t.env).start('screen'), (err: unknown) =>
    err instanceof CaptureError && /Screen sharing was cancelled/.test(err.message),
  )
  assert.equal(t.mic.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('mic denied: the screen picker never opens and no session is made', async () => {
  let screenAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        screenAsked = true
        return stream(t.display)
      },
      getUserMedia: async () => {
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
    },
  })
  await assert.rejects(capture(t.env).start('screen'), /Microphone access was blocked/)
  assert.equal(screenAsked, false)
  assert.equal(FakeRecorder.created.length, 0)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('no microphone found: the screen picker never opens and no session is made', async () => {
  let screenAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        screenAsked = true
        return stream(t.display)
      },
      getUserMedia: async () => {
        throw new DOMException('Requested device not found', 'NotFoundError')
      },
    },
  })
  await assert.rejects(capture(t.env).start('screen'), /No microphone was found/)
  assert.equal(screenAsked, false)
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('stop while the mic prompt is open cancels the start instead of recording; the screen is never asked', async () => {
  let grantMic: (s: unknown) => void = () => {}
  let screenAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        screenAsked = true
        return stream(t.display)
      },
      getUserMedia: () => new Promise((resolve) => (grantMic = resolve)),
    },
  })
  const live = capture(t.env)
  const starting = live.start('screen')
  await new Promise((r) => setTimeout(r, 0))
  const stopping = live.stop()
  grantMic(stream(t.mic))
  await assert.rejects(starting, /cancelled/)
  await stopping
  assert.equal(FakeRecorder.created.length, 0)
  assert.equal(t.mic.stopped, true)
  assert.equal(screenAsked, false)
  assert.equal(t.posted('/end').length, 0)
  assert.equal(t.calls.filter((c) => c.method === 'DELETE').length, 0)
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('no WebM support: a clear error before asking for anything', async () => {
  class NoWebm extends FakeRecorder {
    static isTypeSupported = () => false
  }
  const t = setup({ MediaRecorder: NoWebm })
  await assert.rejects(capture(t.env).start('screen'), /can't record WebM audio/)
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
  await live.start('screen')
  await live.stop()
  assert.deepEqual(ends, [
    { audio_chunks: 1, allow_gaps: false },
    { audio_chunks: 1, allow_gaps: true },
  ])
})

function within<T>(p: Promise<T>, ms = 200): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`still pending after ${ms} ms`)), ms)
  })
  return Promise.race([p, timeout]).finally(() => clearTimeout(timer))
}

const tick = () => new Promise((r) => setTimeout(r, 0))

async function until(ready: () => boolean, ms = 1000) {
  const deadline = Date.now() + ms
  while (!ready()) {
    assert.ok(Date.now() < deadline, `not ready after ${ms} ms`)
    await tick()
  }
}

function frameShares(t: ReturnType<typeof setup>): number[] {
  return t.posted('/frame').map((c) => Number(new URL(c.url, 'http://x').searchParams.get('share')))
}

test('a start failing after a share with tab audio stops every screen track and the mic', async () => {
  const video = new FakeTrack('video')
  const tabAudio = new FakeTrack('audio')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(video, tabAudio),
      getUserMedia: async () => stream(t.mic),
    },
  })
  const live = new LiveCapture(
    async () => {
      throw new Error('engine down')
    },
    fast,
    t.env as never,
  )
  await assert.rejects(live.start('screen'))
  assert.ok(video.stopped && tabAudio.stopped && t.mic.stopped)
  assert.equal(FakeRecorder.created.length, 0)
})

test('stop while the mic prompt stays open cancels without waiting; the screen is never asked', async () => {
  let screenAsked = false
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => {
        screenAsked = true
        return stream(t.display)
      },
      getUserMedia: () => new Promise(() => {}),
    },
  })
  const live = capture(t.env)
  const starting = live.start('screen')
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  assert.equal(screenAsked, false)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('the mic disconnected while the screen picker is open fails the start and stops a screen granted later', async () => {
  let grantScreen: (s: unknown) => void = () => {}
  const t = setup({
    mediaDevices: {
      getDisplayMedia: () => new Promise((resolve) => (grantScreen = resolve)),
      getUserMedia: async () => stream(t.mic),
    },
  })
  const starting = capture(t.env).start('screen')
  await tick()
  t.mic.end()
  await assert.rejects(within(starting), /The microphone was disconnected before recording started/)
  grantScreen(stream(t.display))
  await tick()
  assert.equal(t.display.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test("screen sharing stopped while the Mac's sound is asked for fails the start and releases the mic too", async () => {
  let grantSound: (s: unknown) => void = () => {}
  const t = setup({
    macSound: true,
    mediaDevices: {
      getDisplayMedia: (constraints: { video: boolean }) =>
        constraints.video ? Promise.resolve(stream(t.display)) : new Promise((resolve) => (grantSound = resolve)),
      getUserMedia: async () => stream(t.mic),
    },
  })
  const starting = capture(t.env).start('screen')
  await tick()
  t.display.end()
  await assert.rejects(within(starting), /Screen sharing stopped before recording started/)
  grantSound(stream(new FakeTrack('audio')))
  await tick()
  assert.ok(t.mic.stopped && t.display.stopped)
  assert.equal(FakeRecorder.created.length, 0)
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
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
  await assert.rejects(within(capture(t.env).start('screen')), /could not start recording/)
  assert.ok(t.display.stopped && t.mic.stopped)
  await tick()
  assert.equal(t.posted('/audio').length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the recorder refusing the stream releases the screen, mic and audio mixer', async () => {
  const mixer = fakeMixer()
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
    mixAudio: () => mixer,
  })
  await assert.rejects(capture(t.env).start('screen'), CaptureError)
  assert.ok(t.display.stopped && tabAudio.stopped && t.mic.stopped && mixer.closed)
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
  const starting = live.start('screen')
  await tick()
  assert.equal(FakeRecorder.created.length, 1)
  await within(live.stop())
  fireStart()
  await assert.rejects(within(starting), /cancelled/)
  await tick()
  assert.ok(t.display.stopped && t.mic.stopped)
  assert.equal(t.posted('/audio').length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('stop while the screen picker is open: a screen granted later is stopped, and the mic is released', async () => {
  let grantScreen: (s: unknown) => void = () => {}
  const t = setup({
    mediaDevices: {
      getDisplayMedia: () => new Promise((resolve) => (grantScreen = resolve)),
      getUserMedia: async () => stream(t.mic),
    },
  })
  const live = capture(t.env)
  const starting = live.start('screen')
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  grantScreen(stream(t.display))
  await tick()
  assert.ok(t.display.stopped && t.mic.stopped)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('closing the tab during a prompt makes no session', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: () => new Promise(() => {}),
      getUserMedia: async () => stream(t.mic),
    },
  })
  const starting = capture(t.env).start('screen')
  await tick()
  t.env.page.dispatchEvent(new Event('pagehide'))
  await assert.rejects(within(starting), /cancelled/)
  assert.equal(t.mic.stopped, true)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(t.beacons.length, 0)
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('frame capture failing to start leaves nothing half-started', async () => {
  const t = setup({
    captureFrames: () => {
      throw new Error('no canvas')
    },
  })
  const live = capture(t.env)
  await assert.rejects(live.start('screen'), (err: unknown) => err instanceof Error && err.message === "Recording couldn't start. Try again.")
  assert.ok(t.display.stopped && t.mic.stopped)
  const unload = new Event('beforeunload', { cancelable: true })
  t.env.page.dispatchEvent(unload)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.equal(unload.defaultPrevented, false)
  assert.equal(t.beacons.length, 0)
  await live.stop()
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the recorder failing mid-recording ends only after its last chunk', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  const recorder = FakeRecorder.created[0]
  recorder.emit('a')
  recorder.state = 'inactive'
  recorder.onerror?.()
  await tick()
  assert.equal(t.posted('/end').length, 0, 'still waiting for the last chunk')
  recorder.emit('last')
  recorder.dispatchEvent(new Event('stop'))
  await until(() => t.posted('/end').length === 1)
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
  await live.start('screen')
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
    await live.start('screen')
    assert.deepEqual(asked, [{ video: true, audio: SHARED_AUDIO }, { video: true, audio: false }])
    assert.equal(FakeRecorder.created.length, 1)
    await live.stop()
  })
}

test('the mic alone is recorded through the mixer too: one stream as sources come and go', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('screen')
  assert.equal(t.mixed.length, 1)
  assert.deepEqual(
    t.mixed[0].live.map((s) => s.getTracks()),
    [[t.mic]],
    'a window shared without sound adds nothing to mix',
  )
  await live.stop()
})

function audioSetup(options: { macSound?: boolean; sound?: () => Promise<unknown> } = {}) {
  const asked: unknown[] = []
  const macSound = new FakeTrack('audio')
  const t = setup({
    macSound: options.macSound ?? true,
    mediaDevices: {
      getDisplayMedia: async (c: unknown) => {
        asked.push(c)
        return options.sound ? options.sound() : stream(macSound)
      },
      getUserMedia: async () => stream(t.mic),
    },
  })
  return { ...t, asked, macSound }
}

const sources = (t: { mixed: FakeMixer[] }) => t.mixed[0].live.map((s) => s.getTracks()[0])

test("in a browser, audio records the mic alone: no screen or sound is asked for", async () => {
  const t = audioSetup({ macSound: false })
  const live = capture(t.env)
  assert.deepEqual(await live.start('audio'), { screen: null, macSound: false })
  assert.deepEqual(t.asked, [])
  assert.deepEqual(sources(t), [t.mic])
  t.frame(performance.now())
  FakeRecorder.created[0].emit('a')
  await live.stop()
  assert.equal(t.posted('/frame').length, 0)
  assert.equal(t.posted('/audio').length, 2)
  assert.ok(t.mic.stopped)
})

test("in the app, audio mixes the mic with the Mac's sound, asked for alone without voice processing", async () => {
  const t = audioSetup()
  const live = capture(t.env)
  assert.deepEqual(await live.start('audio'), { screen: null, macSound: true })
  assert.deepEqual(t.asked, [{ video: false, audio: SHARED_AUDIO }])
  assert.deepEqual(sources(t), [t.mic, t.macSound])
  await live.stop()
  assert.ok(t.mic.stopped && t.macSound.stopped)
})

test("the Mac's sound refused (an ended track) or failing: the recording starts with the mic", async () => {
  const refused = new FakeTrack('audio')
  refused.readyState = 'ended'
  const failing = async () => {
    throw new DOMException('Invalid capture constraints', 'AbortError')
  }
  for (const sound of [async () => stream(refused), failing]) {
    const t = audioSetup({ sound })
    const live = capture(t.env)
    assert.deepEqual(await live.start('audio'), { screen: null, macSound: false })
    assert.deepEqual(sources(t), [t.mic])
    FakeRecorder.created[0].emit('a')
    await live.stop()
    assert.equal(t.posted('/audio').length, 2)
  }
})

test("the Mac's sound ending mid-recording is reported once; the recording goes on", async () => {
  const t = audioSetup()
  let reported = 0
  const live = capture(t.env, { onMacSoundEnded: () => reported++ })
  await live.start('audio')
  t.macSound.end()
  assert.equal(reported, 1)
  FakeRecorder.created[0].emit('a')
  await live.stop()
  assert.equal(t.posted('/end').length, 1)
  assert.equal(t.posted('/audio').length, 2)
})

test("the mic denied for audio: the Mac's sound is never asked for and no session is made", async () => {
  const t = audioSetup()
  t.env.mediaDevices.getUserMedia = async () => {
    throw new DOMException('Permission denied', 'NotAllowedError')
  }
  await assert.rejects(capture(t.env).start('audio'), /Microphone access was blocked/)
  assert.deepEqual(t.asked, [])
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test("stop while the Mac's sound is being asked for cancels the start and releases the mic", async () => {
  let grant: (s: unknown) => void = () => {}
  const t = audioSetup({ sound: () => new Promise((resolve) => (grant = resolve)) })
  const live = capture(t.env)
  const starting = live.start('audio')
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  grant(stream(t.macSound))
  await tick()
  assert.ok(t.mic.stopped && t.macSound.stopped)
  assert.equal(FakeRecorder.created.length, 0)
})

test("stop while the Mac's sound is asked for, which then fails: no recorder or mixer is made", async () => {
  let refuse: (err: unknown) => void = () => {}
  const t = audioSetup({ sound: () => new Promise((_, reject) => (refuse = reject)) })
  const live = capture(t.env)
  const starting = live.start('audio')
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  refuse(new DOMException('Invalid capture constraints', 'AbortError'))
  await tick()
  await tick()
  assert.equal(FakeRecorder.created.length, 0)
  assert.equal(t.mixed.length, 0)
  assert.ok(t.mic.stopped)
})

test("the Mac's sound counts as refused only in the app", () => {
  assert.equal(macSoundRefused(true, { macSound: false }), true)
  assert.equal(macSoundRefused(true, { macSound: true }), false)
  assert.equal(macSoundRefused(false, { macSound: false }), false, 'a browser records the mic alone')
})

test('a browser without MediaRecorder says it cannot record audio', async () => {
  const t = audioSetup({ macSound: false })
  await assert.rejects(capture({ ...t.env, MediaRecorder: undefined }).start('audio'), /can't record audio/)
})

function resumableSetup(mac = false) {
  const mics: FakeTrack[] = []
  const t = setup({ macSound: mac })
  t.env.mediaDevices.getUserMedia = async () => {
    const mic = new FakeTrack('audio')
    mics.push(mic)
    return stream(mic)
  }
  return { ...t, mics }
}

test('stop pauses the recording and releases every source; continue carries on in it', async (tc) => {
  const clock = fakeClock(tc)
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('screen')
  const recorder = FakeRecorder.created[0]
  recorder.emit('a')
  clock.advance(1000)
  live.pause()
  assert.equal(recorder.state, 'paused')
  assert.ok(t.display.stopped && t.mics[0].stopped, 'the screen and the mic are let go')
  assert.deepEqual(t.mixed[0].live, [])
  t.frame(clock.now())
  assert.equal(t.posted('/frame').length, 0)
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(t.beacons.map((b) => b.url), ['/api/live/s1/stop'])

  clock.advance(10_000)
  const display2 = new FakeTrack('video')
  t.env.mediaDevices.getDisplayMedia = async () => {
    clock.advance(3000)
    return stream(display2)
  }
  const again = await live.resume()
  assert.equal(again.screen?.getVideoTracks()[0], display2)
  assert.equal(recorder.state, 'recording')
  assert.equal(FakeRecorder.created.length, 1, 'still one recorder, so one audio stream')
  assert.deepEqual(sources(t), [t.mics[1]])
  clock.advance(250)
  t.frame(clock.now())
  assert.deepEqual(frameTimes(t), [1.25])
  recorder.emit('b')
  await live.stop()
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 4, allow_gaps: false })
  assert.ok(display2.stopped && t.mics[1].stopped)
})

test('make notes straight from stopped: the last chunk is sent and the session ended', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  FakeRecorder.created[0].emit('a')
  live.pause()
  await live.stop()
  assert.equal(t.posted('/audio').length, 3)
  assert.equal(t.posted('/end').length, 1)
})

test('a continue that fails (the mic blocked) leaves it stopped, and it can still be finished', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  t.env.mediaDevices.getUserMedia = async () => {
    throw new DOMException('Permission denied', 'NotAllowedError')
  }
  await assert.rejects(live.resume(), /Microphone access was blocked/)
  assert.equal(FakeRecorder.created[0].state, 'paused')
  await live.stop()
  assert.equal(t.posted('/end').length, 1)
})

test('a continue cancelled in the picker carries on with the screen off', async (tc) => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('screen')
  live.pause()
  t.env.mediaDevices.getDisplayMedia = async () => {
    throw new DOMException('Invalid capture constraints', 'AbortError')
  }
  tc.mock.method(console, 'warn', () => {})
  assert.deepEqual(await live.resume(), { screen: null, macSound: false })
  assert.equal(FakeRecorder.created[0].state, 'recording')
  assert.deepEqual(sources(t), [t.mics[1]])
  t.frame(performance.now())
  await tick()
  assert.deepEqual(frameShares(t), [], 'the screen stays off')
  await live.stop()
})

test('finishing while a continue is still asking stops what it is granted afterwards', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  let grant: (s: unknown) => void = () => {}
  t.env.mediaDevices.getUserMedia = () => new Promise((resolve) => (grant = resolve))
  const continuing = live.resume()
  await tick()
  await live.stop()
  const late = new FakeTrack('audio')
  grant(stream(late))
  await assert.rejects(continuing, CaptureError)
  assert.ok(late.stopped)
  assert.equal(t.posted('/end').length, 1)
})

test('discard stops everything, sends nothing more, and deletes the session', async () => {
  const t = resumableSetup(true)
  const live = capture(t.env)
  await live.start('audio')
  const recorder = FakeRecorder.created[0]
  live.pause()
  await live.discard()
  assert.equal(recorder.state, 'inactive')
  assert.ok(t.mixed[0].closed && t.mics[0].stopped)
  recorder.emit('late')
  t.env.page.dispatchEvent(new Event('pagehide'))
  assert.deepEqual(
    t.calls.map((c) => `${c.method} ${c.url}`),
    ['POST /api/live/s1/audio?seq=0', 'DELETE /api/sessions/s1'],
    'the chunk flushed at Stop, then nothing more: no chunk, no end request, no beacon',
  )
  assert.equal(t.beacons.length, 0)
})

test('discard while recording works too, and a failed delete can be retried', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  t.failWhen((url) => url === '/api/sessions/s1')
  await assert.rejects(live.discard(), /wasn't deleted/)
  t.failWhen(() => false)
  await live.discard()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test("a delete answered in place of the engine says Iris isn't responding, and can be retried", async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  const reach = t.env.fetch
  t.env.fetch = async (url, init) =>
    init?.method === 'DELETE' ? new Response('{"detail":"The Iris engine is not running."}', { status: 503 }) : reach(url, init)
  await assert.rejects(live.discard(), /Iris isn't responding, so the recording wasn't deleted/)
  t.env.fetch = reach
  await live.discard()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
})

test('the mic going away while stopped ends nothing: it was let go', async () => {
  const t = resumableSetup()
  const reasons: string[] = []
  const live = capture(t.env, { onEnded: (r: string) => reasons.push(r) })
  await live.start('audio')
  live.pause()
  t.mics[0].end()
  assert.deepEqual(reasons, [])
  await live.stop()
})

test("stop sends what the recorder holds at once, so leaving while stopped doesn't lose it", async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  await tick()
  assert.equal(t.posted('/audio').length, 1, 'uploaded while stopped')
})

test('a failed discard leaves the recording untouched: it can be continued and finished', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  const recorder = FakeRecorder.created[0]
  live.pause()
  t.failWhen((url) => url === '/api/sessions/s1')
  await assert.rejects(live.discard(), /wasn't deleted/)
  t.failWhen(() => false)
  assert.equal(recorder.state, 'paused')
  await live.resume()
  recorder.emit('b')
  await live.stop()
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 3, allow_gaps: false })
})

test("sharing that ends while the Mac's sound is asked for on Continue fails the continue; it stays stopped", async () => {
  const t = resumableSetup(true)
  const live = capture(t.env)
  await live.start('screen')
  live.pause()
  const display2 = new FakeTrack('video')
  let grantSound: (s: unknown) => void = () => {}
  t.env.mediaDevices.getDisplayMedia = (constraints?: { video?: boolean }) =>
    constraints?.video ? Promise.resolve(stream(display2)) : new Promise((resolve) => (grantSound = resolve))
  const continuing = live.resume()
  await tick()
  display2.end()
  grantSound(stream(new FakeTrack('audio')))
  await assert.rejects(continuing, /Screen sharing stopped before the recording continued/)
  assert.equal(FakeRecorder.created[0].state, 'paused')
  assert.deepEqual(t.mixed[0].live, [])
  assert.ok(t.mics[1].stopped && display2.stopped, 'the mic and screen asked for again are let go')
})

test('discard while a continue is still asking deletes the recording and stops what comes late', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  let grant: (s: unknown) => void = () => {}
  t.env.mediaDevices.getUserMedia = () => new Promise((resolve) => (grant = resolve))
  const continuing = live.resume()
  await tick()
  await live.discard()
  const late = new FakeTrack('audio')
  grant(stream(late))
  await assert.rejects(continuing, CaptureError)
  assert.ok(late.stopped)
  assert.equal(t.posted('/end').length, 0)
})

test("the Mac's sound is let go on Stop and asked for again on Continue", async () => {
  const sounds: FakeTrack[] = []
  const t = audioSetup({
    sound: async () => {
      const sound = new FakeTrack('audio')
      sounds.push(sound)
      return stream(sound)
    },
  })
  t.env.mediaDevices.getUserMedia = async () => stream(new FakeTrack('audio'))
  const live = capture(t.env)
  await live.start('audio')
  live.pause()
  assert.ok(sounds[0].stopped)
  assert.deepEqual(t.mixed[0].live, [])
  const again = await live.resume()
  assert.equal(again.macSound, true)
  assert.equal(t.asked.length, 2, 'asked for once per start and continue')
  assert.equal(sources(t)[1], sounds[1])
  await live.stop()
})

test("the recording's clock leaves stopped time out, and the menu bar's start matches it", () => {
  let clock = { recordedMs: 0, since: 1_000 }
  assert.equal(recordedMs(clock, 13_000), 12_000)
  clock = pausedClock(clock, 13_000)
  assert.deepEqual(clock, { recordedMs: 12_000, since: 0 })
  assert.equal(recordedMs(clock, 99_000), 12_000, 'frozen while stopped')
  clock = { ...clock, since: 50_000 }
  assert.equal(recordedMs(clock, 53_000), 15_000)
  assert.equal(53_000 - clockStart(clock), 15_000, "the menu bar's elapsed time")
})

test('the mic is asked for first, then the screen: the session is made only after both are granted', async () => {
  const order: string[] = []
  const t = setup()
  t.env.mediaDevices.getDisplayMedia = async () => (order.push('screen'), stream(t.display))
  t.env.mediaDevices.getUserMedia = async () => (order.push('mic'), stream(t.mic))
  const live = new LiveCapture(async () => (order.push('session'), 's1'), fast, t.env as never)
  await live.start('screen')
  assert.deepEqual(order, ['mic', 'screen', 'session'])
  assert.equal(live.sessionId, 's1')
  await live.stop()
})

test("in the app the mic is asked for first, then the screen, then the Mac's sound", async () => {
  const order: string[] = []
  const t = setup({ macSound: true })
  t.env.mediaDevices.getDisplayMedia = async (constraints?: { video?: boolean }) =>
    constraints?.video ? (order.push('screen'), stream(t.display)) : (order.push('sound'), stream(new FakeTrack('audio')))
  t.env.mediaDevices.getUserMedia = async () => (order.push('mic'), stream(t.mic))
  const live = new LiveCapture(async () => (order.push('session'), 's1'), fast, t.env as never)
  await live.start('screen')
  assert.deepEqual(order, ['mic', 'screen', 'sound', 'session'])
  await live.stop()
})

test("a session that can't be made fails the start and releases what was granted", async () => {
  const t = setup()
  const failing = async (): Promise<string> => {
    throw new CaptureError("Iris isn't responding.")
  }
  const live = new LiveCapture(failing, fast, t.env as never)
  await assert.rejects(live.start('screen'), /Iris isn't responding/)
  assert.ok(t.display.stopped && t.mic.stopped)
  assert.equal(FakeRecorder.created.length, 0)
  await tick()
  assert.deepEqual(t.calls, [], 'no session, so nothing to delete')
})

test('cancelled while the session is being made: the session is deleted', async () => {
  const t = setup()
  let made: (id: string) => void = () => {}
  const live = new LiveCapture(() => new Promise((resolve) => (made = resolve)), fast, t.env as never)
  const starting = live.start('audio')
  await tick()
  const stopping = live.stop()
  made('s1')
  await assert.rejects(starting, /cancelled/)
  await stopping
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
  assert.ok(t.mic.stopped)
})

test('cancelled before the session is made, which arrives later: it is deleted when it does', async () => {
  const t = setup()
  let made: (id: string) => void = () => {}
  const live = new LiveCapture(() => new Promise((resolve) => (made = resolve)), fast, t.env as never)
  const starting = live.start('audio')
  await tick()
  await live.stop()
  await assert.rejects(starting, /cancelled/)
  assert.deepEqual(t.calls, [], 'no session yet, so nothing to delete')
  made('s1')
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), ['DELETE /api/sessions/s1'])
  assert.equal(FakeRecorder.created.length, 0)
})

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
  await assert.rejects(capture(t.env).start('screen'), CaptureError)
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

  const failing = async () => {
    throw new TypeError('Failed to fetch')
  }
  assert.equal(sendBeaconOrKeepalive('/x', body, refuse, failing), true)
  await new Promise((r) => setTimeout(r, 0))
})

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

function shareSetup(options: { macSound?: boolean } = {}) {
  const asked: unknown[] = []
  const shares: { video: FakeTrack; sound: FakeTrack }[] = []
  const macSound = new FakeTrack('audio')
  const mics: FakeTrack[] = []
  const t = setup({ macSound: options.macSound ?? false })
  t.env.mediaDevices.getUserMedia = async () => {
    mics.push(new FakeTrack('audio'))
    return stream(mics.at(-1)!)
  }
  t.env.mediaDevices.getDisplayMedia = async (c: { video?: boolean; audio?: unknown }) => {
    asked.push(c)
    if (!c.video) return stream(macSound)
    const share = { video: new FakeTrack('video'), sound: new FakeTrack('audio') }
    shares.push(share)
    return c.audio ? stream(share.video, share.sound) : stream(share.video)
  }
  return { ...t, asked, shares, macSound, mics }
}

const frameTimes = (t: ReturnType<typeof setup>) =>
  t.posted('/frame').map((c) => Number(new URL(c.url, 'http://x').searchParams.get('t')))

test('the screen turns on and off during an audio recording, with a new share each time', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await live.start('audio')
  t.frame(performance.now())
  const shared = await live.startScreen()
  assert.equal(shared.getVideoTracks()[0], t.shares[0].video)
  assert.deepEqual(t.asked, [{ video: true, audio: SHARED_AUDIO }])
  assert.deepEqual(sources(t), [t.mics[0], t.shares[0].video])
  t.frame(performance.now())
  live.stopScreen()
  assert.ok(t.shares[0].video.stopped && t.shares[0].sound.stopped)
  assert.deepEqual(sources(t), [t.mics[0]], 'its sound is taken out of the mix')
  t.frame(performance.now())
  await live.startScreen()
  t.frame(performance.now())
  await tick()
  assert.deepEqual(frameShares(t), [1, 2])
  assert.equal(FakeRecorder.created.length, 1, 'one recorder throughout: the audio is one stream')
  await live.stop()
  assert.ok(t.shares[1].video.stopped)
})

test("in the app the share is asked for picture only: the Mac's sound is recorded throughout", async () => {
  const t = shareSetup({ macSound: true })
  const live = capture(t.env)
  const started = await live.start('screen')
  assert.equal(started.screen?.getVideoTracks()[0], t.shares[0].video)
  assert.equal(started.macSound, true)
  assert.deepEqual(t.asked, [
    { video: true, audio: false },
    { video: false, audio: SHARED_AUDIO },
  ])
  assert.deepEqual(sources(t), [t.mics[0], t.macSound])
  live.stopScreen()
  await live.startScreen()
  assert.deepEqual(t.asked.at(-1), { video: true, audio: false })
  assert.deepEqual(sources(t), [t.mics[0], t.macSound], 'no share sound: it would be heard twice')
  assert.ok(!t.macSound.stopped, 'the sound capture outlives each share')
  await live.stop()
})

test('a share cancelled in the picker leaves the screen off; the recording carries on', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await live.start('audio')
  t.env.mediaDevices.getDisplayMedia = async () => {
    throw new DOMException('Permission denied', 'NotAllowedError')
  }
  await assert.rejects(live.startScreen(), /Screen sharing was cancelled or blocked/)
  t.frame(performance.now())
  await tick()
  assert.deepEqual(frameShares(t), [])
  assert.equal(FakeRecorder.created[0].state, 'recording')
  await live.stop()
  assert.equal(t.posted('/end').length, 1)
})

test('a share granted after Stop (or after making notes) is let go at once', async () => {
  for (const meanwhile of ['pause', 'stop'] as const) {
    const t = shareSetup()
    const live = capture(t.env)
    await live.start('audio')
    let grant: (s: unknown) => void = () => {}
    t.env.mediaDevices.getDisplayMedia = () => new Promise((resolve) => (grant = resolve))
    const sharing = live.startScreen()
    await tick()
    const ending = meanwhile === 'pause' ? live.pause() : live.stop()
    const late = new FakeTrack('video')
    grant(stream(late))
    assert.equal(await sharing, null, meanwhile)
    await ending
    assert.ok(late.stopped, meanwhile)
    if (meanwhile === 'pause') await live.resume()
    t.frame(performance.now())
    await tick()
    assert.deepEqual(frameShares(t), [], `${meanwhile}: nothing is sent for it`)
    await live.stop()
  }
})

test('the screen can be shared only while recording, one picker at a time', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await assert.rejects(live.startScreen(), CaptureError)
  await live.start('audio')
  const first = live.startScreen()
  assert.equal(live.startScreen(), first, 'a second press while the picker is open is the same share')
  await first
  await assert.rejects(live.startScreen(), CaptureError)
  live.pause()
  await assert.rejects(live.startScreen(), CaptureError)
  live.stopScreen()
  await live.stop()
})

test('Continue after Stop shares the screen again as a new share, on the recording\'s clock', async (tc) => {
  const clock = fakeClock(tc)
  const t = shareSetup()
  const live = capture(t.env)
  await live.start('screen')
  clock.advance(500)
  t.frame(clock.now())
  live.pause()
  clock.advance(10_000)
  const again = await live.resume()
  assert.equal(again.screen?.getVideoTracks()[0], t.shares[1].video, 'the picker opened again')
  clock.advance(250)
  t.frame(clock.now())
  await tick()
  assert.deepEqual(frameShares(t), [1, 2])
  assert.deepEqual(frameTimes(t), [0.5, 0.75], 'the stopped time is left out')
  live.stopScreen()
  live.pause()
  const asked = t.asked.length
  assert.equal((await live.resume()).screen, null)
  assert.equal(t.asked.length, asked)
  await live.stop()
})

test('a share ending while stopped changes nothing: it was let go', async () => {
  const t = shareSetup()
  let screenEnded = 0
  const live = capture(t.env, { onScreenEnded: () => screenEnded++ })
  await live.start('screen')
  live.pause()
  t.shares[0].video.end()
  assert.equal(screenEnded, 0)
  await live.stop()
})

test('a share whose picker is closed because the recording stopped resolves quietly', async () => {
  for (const answer of ['refused', 'granted'] as const) {
    const t = shareSetup()
    const live = capture(t.env)
    await live.start('audio')
    let settle: (s: unknown) => void = () => {}
    let refuse: (e: unknown) => void = () => {}
    t.env.mediaDevices.getDisplayMedia = () => new Promise((resolve, reject) => ((settle = resolve), (refuse = reject)))
    const sharing = live.startScreen()
    await tick()
    live.pause()
    const late = new FakeTrack('video')
    if (answer === 'refused') refuse(new DOMException('Invalid capture constraints', 'AbortError'))
    else settle(stream(late))
    assert.equal(await sharing, null, answer)
    assert.equal(answer === 'granted' ? late.stopped : true, true)
    const asked = t.asked.length
    assert.equal((await live.resume()).screen, null)
    assert.equal(t.asked.length, asked)
    await live.stop()
  }
})

test('a share that has already ended when the picker answers says so', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await live.start('audio')
  const ended = new FakeTrack('video')
  ended.readyState = 'ended'
  t.env.mediaDevices.getDisplayMedia = async () => stream(ended)
  await assert.rejects(live.startScreen(), /stopped before it started/)
  await live.stop()
})
