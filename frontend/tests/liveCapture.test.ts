// LiveCapture lifecycle with fake browser APIs (no real devices, recorder or network).
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
    this.stopped = true // as in browsers: stop() ends the track without an 'ended' event
    this.readyState = 'ended'
  }
  /** The source went away on its own (sharing stopped, macOS refused it). */
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
    this.emit('flushed') // what the recorder held, delivered at once
  }
  pause() {
    this.state = 'paused'
  }
  resume() {
    this.state = 'recording'
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
  sessionsMade = 0
  const calls: Call[] = []
  const beacons: { url: string; body: Blob }[] = []
  const display = new FakeTrack('video')
  const mic = new FakeTrack('audio')
  let onFrame: ((jpeg: Blob, at: number) => void) | undefined
  let failing = (_url: string) => false
  const mixed: FakeMixer[] = [] // every mixer made, with the sources added to it
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
  /** The sources mixed in now. */
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

/** Sessions made by the captures below (`POST /live` stands in as a counter). */
let sessionsMade = 0

function capture(env: object, options = {}) {
  // The fake env stands in for the browser APIs; its shapes are only what LiveCapture uses.
  const createSession = async () => {
    sessionsMade++
    return 's1'
  }
  return new LiveCapture(createSession, { ...fast, ...options }, env as never)
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
  // Finished: leaving the page no longer ends anything, and frames are no longer queued.
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

  t.failWhen(() => false) // the backend is back
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
  t.frame(performance.now()) // none after the share ended
  FakeRecorder.created[0].emit('a')
  await tick()
  assert.equal(t.posted('/frame').length, 1)
  assert.deepEqual(screenChanges(t), [false])
  const stopping = live.stop()
  t.mic.dispatchEvent(new Event('ended')) // ignored while stopping
  await stopping
  assert.deepEqual(reasons, [])
  assert.equal(t.posted('/end').length, 1)
})

test('without an onEnded handler, capture finishes itself when the mic goes away', async () => {
  const t = setup()
  const live = capture(t.env)
  await live.start('audio')
  t.mic.end()
  await new Promise((r) => setTimeout(r, 20))
  assert.equal(t.posted('/end').length, 1)
})

test('leaving the page while recording ends the session with what arrived', async () => {
  const t = setup()
  await capture(t.env).start('screen')
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

test('screen sharing denied: the mic is not requested and no session is made', async () => {
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
  await assert.rejects(capture(t.env).start('screen'), (err: unknown) =>
    err instanceof CaptureError && /Screen sharing was cancelled/.test(err.message),
  )
  assert.equal(micAsked, false)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('mic denied: the screen share is released and no session is made', async () => {
  const display = new FakeTrack('video')
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(display),
      getUserMedia: async () => {
        throw new DOMException('Permission denied', 'NotAllowedError')
      },
    },
  })
  await assert.rejects(capture(t.env).start('screen'), /Microphone access was blocked/)
  assert.equal(display.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  await new Promise((r) => setTimeout(r, 0))
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
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
  const starting = live.start('screen')
  await new Promise((r) => setTimeout(r, 0)) // now waiting on the mic prompt
  const stopping = live.stop()
  grantMic(stream(mic)) // the user allows the mic after leaving the page
  await assert.rejects(starting, /cancelled/)
  await stopping
  assert.equal(FakeRecorder.created.length, 0)
  assert.ok(t.display.stopped && mic.stopped)
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

/** The screen changes posted, in order: true for on, false for off. */
function screenChanges(t: ReturnType<typeof setup>): boolean[] {
  return t.posted('/screen').map((c) => new URL(c.url, 'http://x').searchParams.get('on') === 'true')
}

/** When each screen change was posted for, in seconds of recording. */
function screenTimes(t: ReturnType<typeof setup>): number[] {
  return t.posted('/screen').map((c) => Number(new URL(c.url, 'http://x').searchParams.get('t')))
}

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
  await assert.rejects(capture(t.env).start('screen'), /No microphone was found/)
  assert.ok(video.stopped && tabAudio.stopped)
  await tick()
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('stop while the mic prompt stays open releases the screen share without waiting', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise(() => {}), // the user never answers the prompt
    },
  })
  const live = capture(t.env)
  const starting = live.start('screen')
  await tick() // now waiting on the mic prompt
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  assert.equal(t.display.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('screen sharing stopped during the mic prompt fails the start and releases the mic too', async () => {
  let grantMic: (s: unknown) => void = () => {}
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise((resolve) => (grantMic = resolve)),
    },
  })
  const starting = capture(t.env).start('screen')
  await tick()
  t.display.dispatchEvent(new Event('ended')) // "Stop sharing" in the browser's bar
  await assert.rejects(within(starting), /Screen sharing stopped before recording started/)
  grantMic(stream(t.mic)) // the user answers the prompt afterwards
  await tick()
  assert.equal(t.mic.stopped, true)
  assert.equal(FakeRecorder.created.length, 0)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
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
  const starting = live.start('screen')
  await tick()
  await within(live.stop())
  await assert.rejects(within(starting), /cancelled/)
  grantScreen(stream(t.display))
  await tick()
  assert.equal(t.display.stopped, true)
  assert.equal(micAsked, false)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(sessionsMade, 0, 'asking for permissions makes no session')
})

test('closing the tab during a prompt makes no session', async () => {
  const t = setup({
    mediaDevices: {
      getDisplayMedia: async () => stream(t.display),
      getUserMedia: () => new Promise(() => {}),
    },
  })
  const starting = capture(t.env).start('screen')
  await tick()
  t.env.page.dispatchEvent(new Event('pagehide'))
  await assert.rejects(within(starting), /cancelled/) // a page restored from cache can't record
  assert.equal(t.display.stopped, true)
  assert.deepEqual(t.calls.map((c) => `${c.method} ${c.url}`), [])
  assert.equal(t.beacons.length, 0) // nothing to end: it never recorded
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
  // Nothing to finish: stop after a failed start doesn't end the deleted session.
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

// --- Recording audio only -------------------------------------------------------------------

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
  refused.readyState = 'ended' // what Electron gives once System Audio Recording is refused
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
  grant(stream(t.macSound)) // answered afterwards: stopped at once
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

// --- Stopping, continuing and discarding ----------------------------------------------------

/** A setup whose getUserMedia gives a fresh mic each time (as after a stop and continue). */
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

test('stop pauses the recording and releases every source; continue carries on in it', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('screen')
  const recorder = FakeRecorder.created[0]
  recorder.emit('a')
  live.pause()
  assert.equal(recorder.state, 'paused')
  assert.ok(t.display.stopped && t.mics[0].stopped, 'the screen and the mic are let go')
  assert.deepEqual(t.mixed[0].live, [])
  t.frame(performance.now()) // a frame grabbed as it stopped is not sent
  assert.equal(t.posted('/frame').length, 0)
  t.env.page.dispatchEvent(new Event('pagehide')) // still a recording: leaving ends it
  assert.equal(t.beacons.length, 1)

  const stoppedFor = 60
  await new Promise((r) => setTimeout(r, stoppedFor))
  const display2 = new FakeTrack('video')
  t.env.mediaDevices.getDisplayMedia = async () => stream(display2)
  const resumedAt = performance.now()
  const again = await live.resume()
  assert.equal(again.screen?.getVideoTracks()[0], display2)
  assert.equal(recorder.state, 'recording')
  assert.equal(FakeRecorder.created.length, 1, 'still one recorder, so one audio stream')
  assert.deepEqual(sources(t), [t.mics[1]])
  t.frame(resumedAt)
  const sentT = Number(new URL(t.posted('/frame')[0].url, 'http://x').searchParams.get('t'))
  // Timed on the recording's clock: the stopped time is left out.
  assert.ok(sentT < 0.5 && sentT >= 0, `frame at ${sentT} s`)
  recorder.emit('b')
  await live.stop()
  // 'a', the chunk flushed at Stop, 'b', and the last one: one stream.
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
  assert.equal(t.posted('/audio').length, 3) // 'a', flushed at Stop, the last one
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
  await tick()
  assert.deepEqual(screenChanges(t), [false], 'turned off at Stop, not on again')
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
  // The chunk flushed at Stop, the one after Continue, and the last one: none lost.
  assert.deepEqual(JSON.parse(t.posted('/end')[0].body as string), { audio_chunks: 3, allow_gaps: false })
})

test('sharing that ends while a continue is still asking fails the continue; it stays stopped', async () => {
  const t = resumableSetup()
  const live = capture(t.env)
  await live.start('screen')
  live.pause()
  const display2 = new FakeTrack('video')
  t.env.mediaDevices.getDisplayMedia = async () => stream(display2)
  let grant: (s: unknown) => void = () => {}
  t.env.mediaDevices.getUserMedia = () => new Promise((resolve) => (grant = resolve))
  const continuing = live.resume()
  await tick()
  display2.end() // "Stop sharing" while the mic prompt is open
  grant(stream(new FakeTrack('audio')))
  await assert.rejects(continuing, /Screen sharing stopped before the recording continued/)
  assert.equal(FakeRecorder.created[0].state, 'paused')
  assert.deepEqual(t.mixed[0].live, [])
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
      const sound = new FakeTrack('audio') // a fresh capture each time, as the app gives
      sounds.push(sound)
      return stream(sound)
    },
  })
  t.env.mediaDevices.getUserMedia = async () => stream(new FakeTrack('audio')) // a fresh mic too
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
  clock = pausedClock(clock, 13_000) // Stop at 12 s
  assert.deepEqual(clock, { recordedMs: 12_000, since: 0 })
  assert.equal(recordedMs(clock, 99_000), 12_000, 'frozen while stopped')
  clock = { ...clock, since: 50_000 } // Continue
  assert.equal(recordedMs(clock, 53_000), 15_000)
  assert.equal(53_000 - clockStart(clock), 15_000, "the menu bar's elapsed time")
})

// --- The session is made only once what is recorded has been granted -----------------------

test('the session is made only after the screen and the mic are granted', async () => {
  const order: string[] = []
  const t = setup()
  t.env.mediaDevices.getDisplayMedia = async () => (order.push('screen'), stream(t.display))
  t.env.mediaDevices.getUserMedia = async () => (order.push('mic'), stream(t.mic))
  const live = new LiveCapture(async () => (order.push('session'), 's1'), fast, t.env as never)
  await live.start('screen')
  assert.deepEqual(order, ['screen', 'mic', 'session'])
  assert.equal(live.sessionId, 's1')
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
  await live.stop() // e.g. the page left while POST /live was in flight
  await assert.rejects(starting, /cancelled/)
  assert.deepEqual(t.calls, [], 'no session yet, so nothing to delete')
  made('s1') // the session the engine made meanwhile
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

// --- Turning the screen on and off ----------------------------------------------------------

/** A recording whose screen shares are each a new video track (and, if asked, a tab's sound). */
function shareSetup(options: { macSound?: boolean } = {}) {
  const asked: unknown[] = []
  const shares: { video: FakeTrack; sound: FakeTrack }[] = []
  const macSound = new FakeTrack('audio')
  const mics: FakeTrack[] = [] // a fresh one each time the mic is asked for (Continue)
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
  t.frame(performance.now()) // no screen yet: nothing sent
  const shared = await live.startScreen()
  assert.equal(shared.getVideoTracks()[0], t.shares[0].video)
  // In a browser, a share's tab sound is mixed in while it is shared.
  assert.deepEqual(t.asked, [{ video: true, audio: SHARED_AUDIO }])
  assert.deepEqual(sources(t), [t.mics[0], t.shares[0].video])
  t.frame(performance.now())
  live.stopScreen()
  assert.ok(t.shares[0].video.stopped && t.shares[0].sound.stopped)
  assert.deepEqual(sources(t), [t.mics[0]], 'its sound is taken out of the mix')
  t.frame(performance.now()) // a frame still being grabbed as it was turned off is dropped
  await live.startScreen()
  t.frame(performance.now())
  await tick()
  assert.deepEqual(screenChanges(t), [true, false, true])
  // Sent with no body: the desktop app can't forward an empty one.
  assert.ok(t.posted('/screen').every((c) => c.body === undefined))
  const [on, off, again] = screenTimes(t)
  const frames = frameTimes(t)
  assert.equal(frames.length, 2)
  assert.ok(on <= frames[0] && frames[0] <= off && off <= again && again <= frames[1], `${screenTimes(t)} / ${frames}`)
  assert.equal(FakeRecorder.created.length, 1, 'one recorder throughout: the audio is one stream')
  await live.stop()
  assert.ok(t.shares[1].video.stopped && !t.posted('/screen').some((c) => c.url.includes('t=NaN')))
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
  await tick()
  assert.deepEqual(screenChanges(t), [])
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
    assert.deepEqual(screenChanges(t), [], meanwhile)
  }
})

test('the screen can be shared only while recording, one picker at a time', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await assert.rejects(live.startScreen(), CaptureError) // not started
  await live.start('audio')
  const first = live.startScreen()
  assert.equal(live.startScreen(), first, 'a second press while the picker is open is the same share')
  await first
  await assert.rejects(live.startScreen(), CaptureError) // already shared
  live.pause()
  await assert.rejects(live.startScreen(), CaptureError) // stopped
  live.stopScreen() // nothing to turn off while stopped
  await live.stop()
})

test('Stop turns the screen off at the stopped time and Continue on again at the same time', async () => {
  const t = shareSetup()
  const live = capture(t.env)
  await live.start('screen')
  await new Promise((r) => setTimeout(r, 30))
  live.pause()
  await new Promise((r) => setTimeout(r, 60))
  const again = await live.resume()
  assert.equal(again.screen?.getVideoTracks()[0], t.shares[1].video, 'the picker opened again')
  await tick()
  assert.deepEqual(screenChanges(t), [false, true])
  const [off, on] = screenTimes(t)
  assert.ok(off >= 0.02 && Math.abs(on - off) < 0.01, `off ${off} s, on ${on} s: the stopped time is left out`)
  // With the screen off at Stop, Continue doesn't ask for one.
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
  t.shares[0].video.end() // it was let go
  assert.equal(screenEnded, 0)
  await tick()
  assert.deepEqual(screenChanges(t), [false])
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
    live.pause() // the app then closes its picker, which refuses the request
    const late = new FakeTrack('video')
    if (answer === 'refused') refuse(new DOMException('Invalid capture constraints', 'AbortError'))
    else settle(stream(late))
    assert.equal(await sharing, null, answer)
    assert.equal(answer === 'granted' ? late.stopped : true, true)
    // Continue isn't held up: the screen was off at Stop, so no picker is asked for.
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
