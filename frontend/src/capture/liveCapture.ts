import { irisApp } from '../api/appBridge.ts'
import { engineDidNotAnswer } from '../api/request.ts'
import { CaptureError, describeCaptureError, pickAudioMimeType } from './captureErrors.ts'
import { EndSessionError, MissingAudioError, endLiveSession, stopLiveSession } from './endSession.ts'
import { UploadQueue, type UploadQueueOptions } from './uploadQueue.ts'

const AUDIO_CHUNK_MS = 5000
const FRAME_INTERVAL_MS = 1000
const FRAME_MAX_WIDTH = 1920
const JPEG_QUALITY = 0.8
const DRAIN_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 30_000
const RECORDER_STOP_TIMEOUT_MS = 2000

export type CaptureMode = 'audio' | 'screen'

export type EndReason = 'microphone-ended' | 'recorder-error'

type FinishRequest = 'end' | 'stop'

const START_FAILED: Record<EndReason | 'screen-share-ended', string> = {
  'screen-share-ended': 'Screen sharing stopped before recording started. Share again to start.',
  'microphone-ended': 'The microphone was disconnected before recording started. Try again.',
  'recorder-error': 'The browser could not start recording. Try again.',
}

export interface LiveCaptureOptions {
  onEnded?: (reason: EndReason, message: string) => void
  onScreenEnded?: () => void
  onMacSoundEnded?: () => void
  drainTimeoutMs?: number
  uploads?: UploadQueueOptions
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

export interface CaptureEnv {
  mediaDevices?: Pick<MediaDevices, 'getDisplayMedia' | 'getUserMedia'>
  MediaRecorder?: {
    new (stream: MediaStream, options: MediaRecorderOptions): MediaRecorder
    isTypeSupported(type: string): boolean
  }
  fetch: Fetch
  sendBeacon: (url: string, body: Blob) => boolean
  page: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>
  macSound: boolean
  mixAudio: () => AudioMixer
  captureFrames: (track: MediaStreamTrack, onFrame: (jpeg: Blob, at: number) => void) => () => void
}

export interface AudioMixer {
  stream: MediaStream
  add(source: MediaStream): () => void
  close(): void
}

export function browserEnv(): CaptureEnv {
  return {
    mediaDevices: navigator.mediaDevices,
    MediaRecorder: typeof MediaRecorder === 'undefined' ? undefined : MediaRecorder,
    fetch: (input, init) => fetch(input, init),
    sendBeacon: (url, body) => sendBeaconOrKeepalive(url, body, navigator.sendBeacon.bind(navigator), fetch),
    page: window,
    macSound: Boolean(irisApp()),
    mixAudio,
    captureFrames,
  }
}

export function macSoundRefused(desktopApp: boolean, started: Pick<StartedRecording, 'macSound'>): boolean {
  return desktopApp && !started.macSound
}

export interface RecordingClock {
  recordedMs: number
  since: number
}

export function recordedMs(clock: RecordingClock, now: number): number {
  return clock.recordedMs + (clock.since ? Math.max(0, now - clock.since) : 0)
}

export function pausedClock(clock: RecordingClock, now: number): RecordingClock {
  return { recordedMs: recordedMs(clock, now), since: 0 }
}

export function clockStart(clock: RecordingClock): number {
  return clock.since - clock.recordedMs
}

export interface StartedRecording {
  screen: MediaStream | null
  macSound: boolean
}

export class LiveCapture {
  private id?: string
  private readonly createSession: () => Promise<string>
  private readonly options: LiveCaptureOptions
  private readonly env: CaptureEnv
  private readonly queue: UploadQueue
  private recorder?: MediaRecorder
  private recorderStopped?: Promise<unknown>
  private sources: { stream: MediaStream; unmix?: () => void }[] = []
  private mixer?: AudioMixer
  private display?: MediaStream
  private stopFrames?: () => void
  private screenAtPause = false
  private shares = 0
  private sharing?: Promise<MediaStream | null>
  private startedAt = 0
  private pausedMs = 0
  private pausedAt = 0
  private seq = 0
  private recording = false
  private paused = false
  private resuming?: Promise<StartedRecording>
  private finished = false
  private startFailed = false
  private sessionDiscarded = false
  private opening?: Promise<StartedRecording>
  private cancelled = false
  private cancelOpening?: (err: CaptureError) => void
  private stopping?: Promise<void>
  private stoppingWith?: FinishRequest
  private notesChosen = false

  constructor(
    createSession: () => Promise<string>,
    options: LiveCaptureOptions = {},
    env: CaptureEnv = browserEnv(),
  ) {
    this.createSession = createSession
    this.options = options
    this.env = env
    this.queue = new UploadQueue(async (path, body) => {
      const res = await env.fetch(`/api/live/${this.id}/${path}`, {
        method: 'POST',
        body,
        signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
      })
      return res.status
    }, options.uploads)
  }

  get sessionId(): string | undefined {
    return this.id
  }

  get pendingUploads(): number {
    return this.queue.pending
  }

  async start(mode: CaptureMode): Promise<StartedRecording> {
    const cancelled = new Promise<never>((_, reject) => (this.cancelOpening = reject))
    this.opening = Promise.race([this.open(mode), cancelled])
    this.env.page.addEventListener('pagehide', this.onPageHideWhileStarting)
    try {
      return await this.opening
    } catch (err) {
      this.startFailed = true
      if (this.recorder) this.recorder.ondataavailable = null
      this.release()
      this.discardSession()
      if (err instanceof CaptureError) throw err
      throw new CaptureError("Recording couldn't start. Try again.", { cause: err })
    } finally {
      this.env.page.removeEventListener('pagehide', this.onPageHideWhileStarting)
      this.opening = undefined
      this.cancelOpening = undefined
    }
  }

  stop(): Promise<void> {
    this.notesChosen = true
    return this.finishWith('end')
  }

  keep(): Promise<void> {
    return this.finishWith(this.notesChosen ? 'end' : 'stop')
  }

  private finishWith(request: FinishRequest): Promise<void> {
    if (this.stopping && (request === 'stop' || this.stoppingWith === 'end')) return this.stopping
    const kept = this.stopping?.catch(() => {})
    const finishing: Promise<void> = (kept ? kept.then(() => this.finish(request)) : this.finish(request)).catch(
      (err: unknown) => {
        if (this.stopping === finishing) this.stopping = undefined
        throw err
      },
    )
    this.stopping = finishing
    this.stoppingWith = request
    return finishing
  }

  private async open(mode: CaptureMode): Promise<StartedRecording> {
    const { MediaRecorder: Recorder } = this.env
    const screen = mode === 'screen'
    const devices = this.env.mediaDevices
    if (!devices?.getUserMedia || !Recorder || (screen && !devices.getDisplayMedia)) {
      throw new CaptureError(
        `This browser can't record ${screen ? 'the screen' : 'audio'}. Use a recent desktop Chrome, Edge or Firefox.`,
      )
    }
    const mimeType = pickAudioMimeType((type) => Recorder.isTypeSupported(type))
    if (!mimeType) {
      throw new CaptureError(
        "This browser can't record WebM audio. Use a recent desktop Chrome, Edge or Firefox.",
      )
    }

    const got = await this.acquire(screen)
    this.id = await this.createSession()
    if (this.cancelled) {
      this.discardSession()
      this.throwIfCancelled()
    }
    const mixer = this.env.mixAudio()
    this.mixer = mixer
    this.mixIn()
    const recorder = new Recorder(mixer.stream, { mimeType })
    this.recorder = recorder
    this.recorderStopped = new Promise((resolve) => recorder.addEventListener('stop', resolve, { once: true }))
    recorder.ondataavailable = (e) => {
      if (e.data.size) this.queue.add('audio', `audio?seq=${this.seq++}`, e.data)
    }
    recorder.onerror = () => this.ended('recorder-error', 'Recording stopped: the browser failed.')
    const started = new Promise((resolve) => recorder.addEventListener('start', resolve, { once: true }))
    recorder.start(AUDIO_CHUNK_MS)
    await started
    this.throwIfCancelled()
    this.startedAt = performance.now()
    if (got.screen) this.captureScreen(got.screen)
    this.env.page.addEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.addEventListener('pagehide', this.onPageHide)
    this.recording = true
    return this.started(got)
  }

  pause(): void {
    if (!this.recording || this.paused || this.stopping || this.resuming) return
    this.paused = true
    this.pausedAt = performance.now()
    this.screenAtPause = Boolean(this.display)
    if (this.recorder?.state === 'recording') {
      this.recorder.requestData()
      this.recorder.pause()
    }
    this.stopFrames?.()
    this.stopFrames = undefined
    this.display = undefined
    this.releaseSources()
  }

  resume(): Promise<StartedRecording> {
    if (!this.paused || this.stopping) return Promise.reject(new CaptureError('The recording is not stopped.'))
    this.resuming ??= (async () => {
      try {
        const got = await this.acquire(this.screenAtPause, { screenOptional: true })
        if (!this.paused || this.stopping) throw new CaptureError('The recording was finished meanwhile.')
        if (got.screen && got.screen.getVideoTracks()[0]?.readyState !== 'live') {
          throw new CaptureError('Screen sharing stopped before the recording continued. Continue again to share.')
        }
        if (!hasLiveAudio(got.mic)) {
          throw new CaptureError('The microphone was disconnected before the recording continued. Try again.')
        }
        this.pausedMs += performance.now() - this.pausedAt
        this.mixIn()
        if (this.recorder?.state === 'paused') this.recorder.resume()
        this.paused = false
        if (got.screen) this.captureScreen(got.screen)
        return this.started(got)
      } catch (err) {
        this.releaseSources()
        if (err instanceof CaptureError) throw err
        throw new CaptureError("Recording couldn't continue. Try again.", { cause: err })
      } finally {
        this.resuming = undefined
      }
    })()
    return this.resuming
  }

  startScreen(): Promise<MediaStream | null> {
    if (!this.recording || this.paused || this.stopping || this.resuming || this.display) {
      return Promise.reject(new CaptureError('The screen can be shared only while recording.'))
    }
    if (!canShareScreen(this.env.mediaDevices)) {
      return Promise.reject(new CaptureError("This browser can't share the screen."))
    }
    const stillRecording = () => this.recording && !this.paused && !this.stopping
    this.sharing ??= (async () => {
      try {
        let display: MediaStream
        try {
          display = await getDisplay(this.env.mediaDevices!, !this.env.macSound)
        } catch (err) {
          if (!stillRecording()) return null
          throw err
        }
        if (!stillRecording()) {
          display.getTracks().forEach((t) => t.stop())
          return null
        }
        if (display.getVideoTracks()[0]?.readyState !== 'live') {
          display.getTracks().forEach((t) => t.stop())
          throw new CaptureError('Screen sharing stopped before it started. Share the screen again.')
        }
        this.sources.push({ stream: display })
        this.listenForScreenEnd(display)
        this.mixIn()
        this.captureScreen(display)
        return display
      } finally {
        this.sharing = undefined
      }
    })()
    return this.sharing
  }

  stopScreen(): void {
    if (!this.display || !this.recording || this.paused || this.stopping) return
    this.releaseDisplay()
  }

  async discard(): Promise<void> {
    if (this.opening) {
      this.cancel('Recording was cancelled before it started.')
      await this.opening.catch(() => {})
      return
    }
    const notDeleted = "Iris isn't responding, so the recording wasn't deleted. Try again."
    const res = await this.env
      .fetch(`/api/sessions/${this.id}`, { method: 'DELETE' })
      .catch((err: unknown) => {
        throw new CaptureError(notDeleted, { cause: err })
      })
    if (engineDidNotAnswer(res.status)) throw new CaptureError(notDeleted)
    if (!res.ok && res.status !== 404) {
      throw new CaptureError("Iris couldn't delete the recording. Try again.")
    }
    this.recording = false
    this.paused = false
    this.finished = true
    this.queue.close()
    if (this.recorder) this.recorder.ondataavailable = null
    this.release()
    this.env.page.removeEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.removeEventListener('pagehide', this.onPageHide)
  }

  private async acquire(
    screen: boolean,
    { screenOptional = false } = {},
  ): Promise<{ screen: MediaStream | null; mic: MediaStream; sound: MediaStream | null }> {
    const devices = this.env.mediaDevices!
    let mic: MediaStream
    try {
      mic = this.acquired(await devices.getUserMedia({ audio: true }))
    } catch (err) {
      if (err instanceof CaptureError) throw err
      throw new CaptureError(describeCaptureError(err, 'microphone'), { cause: err })
    }
    mic.getAudioTracks()[0]?.addEventListener('ended', () =>
      this.ended('microphone-ended', 'The microphone was disconnected, so the recording was finished.'),
    )
    let display: MediaStream | null = null
    if (screen) {
      try {
        display = this.acquired(await getDisplay(devices, !this.env.macSound))
      } catch (err) {
        if (!screenOptional || !(err instanceof CaptureError) || this.cancelled || this.stopping) throw err
        console.warn('The screen was not shared again; continuing with the screen off', err)
      }
    }
    if (display) this.listenForScreenEnd(display)
    const sound = this.env.macSound ? await getMacSound(devices) : null
    if (sound) this.acquired(sound)
    this.throwIfCancelled()
    sound?.getAudioTracks()[0]?.addEventListener('ended', this.onMacSoundEnded)
    return { screen: display, mic, sound }
  }

  private mixIn() {
    for (const source of this.sources) {
      if (!source.unmix && hasLiveAudio(source.stream)) source.unmix = this.mixer!.add(source.stream)
    }
  }

  private captureScreen(display: MediaStream) {
    const track = display.getVideoTracks()[0]
    if (!track) return
    this.display = display
    const share = ++this.shares
    this.stopFrames = this.env.captureFrames(track, (jpeg, at) => {
      if (!this.recording || this.paused || this.display !== display) return
      this.queue.add('frame', `frame?t=${this.recordedSeconds(at).toFixed(3)}&share=${share}`, jpeg)
    })
  }

  private recordedSeconds(at: number): number {
    return (at - this.startedAt - this.pausedMs) / 1000
  }

  private listenForScreenEnd(display: MediaStream) {
    display.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (this.opening && !this.recording) return this.cancel(START_FAILED['screen-share-ended'])
      if (this.display !== display || this.paused || this.stopping) return
      this.releaseDisplay()
      this.options.onScreenEnded?.()
    })
  }

  private releaseDisplay() {
    const display = this.display
    this.stopFrames?.()
    this.stopFrames = undefined
    this.display = undefined
    for (const source of this.sources.filter((s) => s.stream === display)) {
      source.unmix?.()
      source.stream.getTracks().forEach((t) => t.stop())
    }
    this.sources = this.sources.filter((s) => s.stream !== display)
  }

  private started(got: { screen: MediaStream | null; sound: MediaStream | null }): StartedRecording {
    return { screen: got.screen, macSound: got.sound ? hasLiveAudio(got.sound) : false }
  }

  private throwIfCancelled() {
    if (this.cancelled) throw new CaptureError('Recording was cancelled before it started.')
  }

  private acquired(stream: MediaStream): MediaStream {
    if (this.cancelled || (this.resuming && (this.stopping || !this.recording))) {
      stream.getTracks().forEach((t) => t.stop())
      this.throwIfCancelled()
      throw new CaptureError('The recording was finished meanwhile.')
    }
    this.sources.push({ stream })
    return stream
  }

  private cancel(message: string) {
    this.cancelled = true
    this.cancelOpening?.(new CaptureError(message))
  }

  private onMacSoundEnded = () => {
    if (this.recording && !this.paused && !this.stopping) this.options.onMacSoundEnded?.()
  }

  private ended(reason: EndReason, message: string) {
    if (this.opening && !this.recording) return this.cancel(START_FAILED[reason])
    if (!this.recording || this.paused || this.stopping) return
    if (this.options.onEnded) this.options.onEnded(reason, message)
    else void this.stop().catch(() => {})
  }

  private async finish(request: FinishRequest): Promise<void> {
    if (this.opening) {
      this.cancel('Recording was cancelled before it started.')
      await this.opening.catch(() => {})
      return
    }
    if (this.startFailed) return
    if (this.recording) {
      this.recording = false
      this.paused = false
      this.stopFrames?.()
      const recorder = this.recorder
      if (recorder) {
        if (recorder.state !== 'inactive') recorder.stop()
        await settledWithin(this.recorderStopped, RECORDER_STOP_TIMEOUT_MS)
      }
      this.release()
    }
    const pending = await this.queue.drain(this.options.drainTimeoutMs ?? DRAIN_TIMEOUT_MS)
    if (pending) {
      throw new EndSessionError(
        `${pending} uploads haven't reached Iris yet. Try again in a moment.`,
      )
    }
    const rejectedAudio = this.queue.rejected.filter((r) => r.kind === 'audio')
    if (rejectedAudio.length) console.warn('Iris rejected audio chunks; they become silence', rejectedAudio)
    if (request === 'stop') {
      await stopLiveSession(this.id!, this.env.fetch)
    } else {
      const end = { audioChunks: this.seq, allowGaps: rejectedAudio.length > 0 }
      try {
        await endLiveSession(this.id!, end, this.env.fetch)
      } catch (err) {
        if (!(err instanceof MissingAudioError)) throw err
        console.warn('Iris is missing audio chunks; they become silence', err.missing)
        await endLiveSession(this.id!, { ...end, allowGaps: true }, this.env.fetch)
      }
    }
    this.finished = true
    this.env.page.removeEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.removeEventListener('pagehide', this.onPageHide)
  }

  private release() {
    this.stopFrames?.()
    this.stopFrames = undefined
    this.display = undefined
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop()
    }
    this.releaseSources()
    this.mixer?.close()
    this.mixer = undefined
  }

  private releaseSources() {
    for (const { stream, unmix } of this.sources) {
      unmix?.()
      stream.getTracks().forEach((t) => t.stop())
    }
    this.sources = []
  }

  private onPageHideWhileStarting = () => this.cancel('Recording was cancelled before it started.')

  private discardSession() {
    if (!this.id || this.sessionDiscarded) return
    this.sessionDiscarded = true
    const url = `/api/sessions/${this.id}`
    void this.env.fetch(url, { method: 'DELETE', keepalive: true }).catch(() => {})
  }

  private onBeforeUnload = (e: Event) => {
    if (this.finished) return
    e.preventDefault()
    ;(e as BeforeUnloadEvent).returnValue = ''
  }

  private onPageHide = () => {
    if (this.finished) return
    const [action, request] = this.notesChosen ? ['end', { allow_gaps: true }] : ['stop', {}]
    const body = new Blob([JSON.stringify(request)], { type: 'application/json' })
    this.env.sendBeacon(`/api/live/${this.id}/${action}`, body)
  }
}

async function settledWithin(p: Promise<unknown> | undefined, ms: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise((resolve) => (timer = setTimeout(resolve, ms)))
  try {
    await Promise.race([p, timeout])
  } finally {
    clearTimeout(timer)
  }
}

export const SHARED_AUDIO: MediaTrackConstraints = {
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
}

export function sendBeaconOrKeepalive(
  url: string,
  body: Blob,
  sendBeacon: (url: string, body: Blob) => boolean,
  fetchFn: (url: string, init: RequestInit) => Promise<unknown>,
): boolean {
  try {
    return sendBeacon(url, body)
  } catch {
    const headers = body.type ? { 'content-type': body.type } : undefined
    void fetchFn(url, { method: 'POST', body, headers, keepalive: true }).catch(() => {})
    return true
  }
}

export function canShareScreen(devices: Pick<MediaDevices, 'getDisplayMedia'> | undefined): boolean {
  return typeof devices?.getDisplayMedia === 'function'
}

function hasLiveAudio(stream: MediaStream): boolean {
  return stream.getAudioTracks().some((t) => t.readyState === 'live')
}

async function getMacSound(devices: Pick<MediaDevices, 'getDisplayMedia'>): Promise<MediaStream | null> {
  try {
    return await devices.getDisplayMedia({ video: false, audio: SHARED_AUDIO })
  } catch (err) {
    console.warn("The Mac's sound can't be recorded; recording the microphone alone", err)
    return null
  }
}

async function getDisplay(devices: Pick<MediaDevices, 'getDisplayMedia'>, withSound: boolean): Promise<MediaStream> {
  if (withSound) {
    try {
      return await devices.getDisplayMedia({ video: true, audio: SHARED_AUDIO })
    } catch (err) {
      const name = err instanceof Error || err instanceof DOMException ? err.name : ''
      if (name !== 'TypeError' && name !== 'NotSupportedError') {
        throw new CaptureError(describeCaptureError(err, 'screen'), { cause: err })
      }
    }
  }
  try {
    return await devices.getDisplayMedia({ video: true, audio: false })
  } catch (err) {
    throw new CaptureError(describeCaptureError(err, 'screen'), { cause: err })
  }
}

function mixAudio(): AudioMixer {
  const ctx = new AudioContext()
  void ctx.resume()
  const dest = ctx.createMediaStreamDestination()
  return {
    stream: dest.stream,
    add(source) {
      const node = ctx.createMediaStreamSource(source)
      node.connect(dest)
      return () => node.disconnect()
    },
    close: () => void ctx.close().catch(() => {}),
  }
}

function captureFrames(track: MediaStreamTrack, onFrame: (jpeg: Blob, at: number) => void): () => void {
  const grabber = frameGrabber(track)
  const canvas = document.createElement('canvas')
  let busy = false
  const stopTicker = startTicker(FRAME_INTERVAL_MS, async () => {
    if (busy) return
    busy = true
    try {
      const at = performance.now()
      const jpeg = await grabber.grab(canvas)
      if (jpeg) onFrame(jpeg, at)
    } catch {
    } finally {
      busy = false
    }
  })
  return () => {
    stopTicker()
    grabber.close()
  }
}

interface FrameGrabber {
  grab(canvas: HTMLCanvasElement): Promise<Blob | null>
  close(): void
}

interface ImageCaptureLike {
  grabFrame(): Promise<ImageBitmap>
}

function frameGrabber(track: MediaStreamTrack): FrameGrabber {
  const ImageCaptureCtor = (globalThis as { ImageCapture?: new (t: MediaStreamTrack) => ImageCaptureLike })
    .ImageCapture
  if (ImageCaptureCtor) {
    const capture = new ImageCaptureCtor(track)
    return {
      async grab(canvas) {
        const bitmap = await capture.grabFrame()
        try {
          return await encodeFrame(canvas, bitmap, bitmap.width, bitmap.height)
        } finally {
          bitmap.close()
        }
      },
      close() {},
    }
  }
  const video = document.createElement('video')
  video.srcObject = new MediaStream([track])
  video.muted = true
  void video.play().catch(() => {})
  return {
    async grab(canvas) {
      if (!video.videoWidth) return null
      return encodeFrame(canvas, video, video.videoWidth, video.videoHeight)
    },
    close() {
      video.srcObject = null
    },
  }
}

function encodeFrame(
  canvas: HTMLCanvasElement,
  source: CanvasImageSource,
  width: number,
  height: number,
): Promise<Blob | null> {
  const scale = Math.min(1, FRAME_MAX_WIDTH / width)
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  canvas.getContext('2d')!.drawImage(source, 0, 0, canvas.width, canvas.height)
  return new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', JPEG_QUALITY))
}

export type StartWorker = (intervalMs: number, onMessage: () => void, onError: (event: Event) => void) => () => void

export function startTicker(intervalMs: number, tick: () => void, startWorker: StartWorker = startTimerWorker): () => void {
  let stopped = false
  let pageTimer: ReturnType<typeof setInterval> | undefined
  let terminateWorker: (() => void) | undefined
  const usingWorker = () => !stopped && pageTimer === undefined

  const fallBack = (why: string, cause: unknown) => {
    if (!usingWorker()) return
    console.warn(`The frame timer ${why}; using a page timer instead`, cause)
    terminateWorker?.()
    pageTimer = setInterval(tick, intervalMs)
  }

  try {
    terminateWorker = startWorker(
      intervalMs,
      () => {
        if (usingWorker()) tick()
      },
      (event) => fallBack('worker failed', event),
    )
    if (pageTimer !== undefined) terminateWorker()
  } catch (err) {
    fallBack('worker could not start', err)
  }

  return () => {
    stopped = true
    terminateWorker?.()
    clearInterval(pageTimer)
  }
}

const startTimerWorker: StartWorker = (intervalMs, onMessage, onError) => {
  const worker = new Worker(new URL('./frameTicker.worker.ts', import.meta.url), { type: 'module' })
  worker.onmessage = () => onMessage()
  worker.onerror = onError
  worker.postMessage(intervalMs)
  return () => worker.terminate()
}
