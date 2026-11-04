// Browser live capture: mic (plus tab/system audio if shared) and a screen share.
// Audio goes up as MediaRecorder chunks every few seconds; screen frames as JPEGs at a low fixed
// rate, timed on the audio's clock. Change detection happens in the backend, not here
// (docs/architecture/live.md). Uploads are retried until they land (uploadQueue.ts), and
// ending the session waits for all of them, so a dropped request doesn't lose content.

import { CaptureError, describeCaptureError, pickAudioMimeType } from './captureErrors.ts'
import { EndSessionError, MissingAudioError, endLiveSession } from './endSession.ts'
import { UploadQueue, type UploadQueueOptions } from './uploadQueue.ts'

const AUDIO_CHUNK_MS = 5000
const FRAME_INTERVAL_MS = 1000
const FRAME_MAX_WIDTH = 1920 // the backend scales further (IRIS_FRAME_MAX_WIDTH)
const JPEG_QUALITY = 0.8
const DRAIN_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 30_000 // a hung request becomes a retry instead of blocking the queue
const RECORDER_STOP_TIMEOUT_MS = 2000 // a recorder that never reports 'stop' doesn't block ending

export type EndReason = 'screen-share-ended' | 'microphone-ended' | 'recorder-error'

/** Why `start()` failed when capture ended before recording began. */
const START_FAILED: Record<EndReason, string> = {
  'screen-share-ended': 'Screen sharing stopped before recording started. Share again to start.',
  'microphone-ended': 'The microphone was disconnected before recording started. Try again.',
  'recorder-error': 'The browser could not start recording. Try again.',
}

export interface LiveCaptureOptions {
  /**
   * Capture ended on its own: the user stopped sharing, the mic went away, or the recorder
   * failed. Call `stop()` to finish the session. Without a handler, capture stops itself.
   */
  onEnded?: (reason: EndReason, message: string) => void
  /** How long `stop()` waits for pending uploads before giving up (it can be called again). */
  drainTimeoutMs?: number
  uploads?: UploadQueueOptions
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

/** The browser APIs capture uses. Injectable, so the lifecycle can be tested without a browser. */
export interface CaptureEnv {
  mediaDevices?: Pick<MediaDevices, 'getDisplayMedia' | 'getUserMedia'>
  MediaRecorder?: {
    new (stream: MediaStream, options: MediaRecorderOptions): MediaRecorder
    isTypeSupported(type: string): boolean
  }
  fetch: Fetch
  sendBeacon: (url: string, body: Blob) => boolean
  /** Where page lifecycle events (`beforeunload`, `pagehide`) arrive: `window`. */
  page: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>
  /** Mix several audio streams into one. */
  mixAudio: (streams: MediaStream[]) => { stream: MediaStream; close(): void }
  /** Grab frames from `track` about once a second; returns a function that stops. */
  captureFrames: (track: MediaStreamTrack, onFrame: (jpeg: Blob, at: number) => void) => () => void
}

export function browserEnv(): CaptureEnv {
  return {
    mediaDevices: navigator.mediaDevices,
    MediaRecorder: typeof MediaRecorder === 'undefined' ? undefined : MediaRecorder,
    fetch: (input, init) => fetch(input, init),
    sendBeacon: (url, body) => navigator.sendBeacon(url, body),
    page: window,
    mixAudio,
    captureFrames,
  }
}

export class LiveCapture {
  readonly sessionId: string
  private readonly options: LiveCaptureOptions
  private readonly env: CaptureEnv
  private readonly queue: UploadQueue
  private recorder?: MediaRecorder
  /** Settles on the recorder's 'stop' event, which follows its last chunk. */
  private recorderStopped?: Promise<unknown>
  private streams: MediaStream[] = []
  private mixer?: { close(): void }
  private stopFrames?: () => void
  private startedAt = 0
  private seq = 0
  private recording = false
  private finished = false
  private startFailed = false
  private opening?: Promise<MediaStream>
  private cancelled = false
  private cancelOpening?: (err: CaptureError) => void
  private stopping?: Promise<void>

  constructor(sessionId: string, options: LiveCaptureOptions = {}, env: CaptureEnv = browserEnv()) {
    this.sessionId = sessionId
    this.options = options
    this.env = env
    this.queue = new UploadQueue(async (path, body) => {
      const res = await env.fetch(`/api/live/${sessionId}/${path}`, {
        method: 'POST',
        body,
        signal: AbortSignal.timeout(UPLOAD_TIMEOUT_MS),
      })
      return res.status
    }, options.uploads)
  }

  /** Uploads not yet acknowledged by the backend. */
  get pendingUploads(): number {
    return this.queue.pending
  }

  /**
   * Ask for the screen and the mic, then start recording. On failure (permission denied, the
   * picker cancelled, an unsupported browser, sharing stopped or the recorder failing before
   * recording began, or `stop()` called meanwhile) everything acquired is released at once, the
   * empty session is deleted, and a `CaptureError` with a user-facing message is thrown. A
   * failure while a permission prompt is still open doesn't wait for the prompt: a stream granted
   * afterwards is stopped as soon as it arrives.
   */
  async start(): Promise<MediaStream> {
    const cancelled = new Promise<never>((_, reject) => (this.cancelOpening = reject))
    this.opening = Promise.race([this.open(), cancelled])
    // Closing the tab during a prompt runs no React cleanup: fail the start on the way out.
    this.env.page.addEventListener('pagehide', this.onPageHideWhileStarting)
    try {
      return await this.opening
    } catch (err) {
      this.startFailed = true
      // A recorder that failed while starting can still deliver a chunk: nothing to upload.
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

  /**
   * Stop capture, upload everything still pending, then tell the backend to process the
   * session. Throws if uploads or the end request fail; nothing captured is discarded, so
   * calling `stop()` again retries. Called while `start()` is still asking for permissions, it
   * cancels the start instead (nothing was recorded, so the session is discarded). After a
   * failed start there is nothing to finish, so it resolves without contacting the backend.
   */
  stop(): Promise<void> {
    this.stopping ??= this.finish().catch((err: unknown) => {
      this.stopping = undefined
      throw err
    })
    return this.stopping
  }

  private async open(): Promise<MediaStream> {
    const { mediaDevices: devices, MediaRecorder: Recorder } = this.env
    if (!devices?.getDisplayMedia || !Recorder) {
      throw new CaptureError(
        "This browser can't record the screen. Use a recent desktop Chrome, Edge or Firefox.",
      )
    }
    const mimeType = pickAudioMimeType((type) => Recorder.isTypeSupported(type))
    if (!mimeType) {
      throw new CaptureError(
        "This browser can't record WebM audio. Use a recent desktop Chrome, Edge or Firefox.",
      )
    }

    const display = this.acquired(await getDisplay(devices))
    // Listen from the start: sharing stopped during the mic prompt must fail the start too.
    display.getVideoTracks()[0]?.addEventListener('ended', () =>
      this.ended('screen-share-ended', 'Screen sharing stopped, so the recording was finished.'),
    )
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

    const recorder = new Recorder(this.audioToRecord(display, mic), { mimeType })
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
    // The audio's time 0: WebM timestamps start at the first recorded sample.
    this.startedAt = performance.now()
    const track = display.getVideoTracks()[0]
    if (track) {
      this.stopFrames = this.env.captureFrames(track, (jpeg, at) => {
        if (!this.recording) return
        this.queue.add('frame', `frame?t=${((at - this.startedAt) / 1000).toFixed(3)}`, jpeg)
      })
    }
    // Nothing below can fail, so a start that threw never looks like a recording.
    this.env.page.addEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.addEventListener('pagehide', this.onPageHide)
    this.recording = true
    return display
  }

  private throwIfCancelled() {
    if (this.cancelled) throw new CaptureError('Recording was cancelled before it started.')
  }

  /** Keep `stream` for release, or stop it at once if the start was cancelled meanwhile. */
  private acquired(stream: MediaStream): MediaStream {
    if (this.cancelled) {
      stream.getTracks().forEach((t) => t.stop())
      this.throwIfCancelled()
    }
    this.streams.push(stream)
    return stream
  }

  /** Fail a start in progress now, without waiting for whatever it is awaiting (a prompt). */
  private cancel(message: string) {
    this.cancelled = true
    this.cancelOpening?.(new CaptureError(message))
  }

  /** The mic alone, or the mic mixed with the shared tab/system audio into one track. */
  private audioToRecord(display: MediaStream, mic: MediaStream): MediaStream {
    if (!display.getAudioTracks().length) return mic
    const mixed = this.env.mixAudio([display, mic])
    this.mixer = mixed
    return mixed.stream
  }

  private ended(reason: EndReason, message: string) {
    if (this.opening && !this.recording) return this.cancel(START_FAILED[reason])
    if (!this.recording || this.stopping) return
    if (this.options.onEnded) this.options.onEnded(reason, message)
    else void this.stop().catch(() => {})
  }

  private async finish(): Promise<void> {
    if (this.opening) {
      // start() fails now, releasing what it has and discarding the session.
      this.cancel('Recording was cancelled before it started.')
      await this.opening.catch(() => {})
      return
    }
    if (this.startFailed) return
    if (this.recording) {
      this.recording = false
      this.stopFrames?.()
      const recorder = this.recorder
      if (recorder) {
        // A recorder that failed is already inactive but still delivers its last chunk, then
        // 'stop'. Either way, wait for that chunk so the chunk count sent to the backend is final.
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
    const request = { audioChunks: this.seq, allowGaps: rejectedAudio.length > 0 }
    try {
      await endLiveSession(this.sessionId, request, this.env.fetch)
    } catch (err) {
      if (!(err instanceof MissingAudioError)) throw err
      // Every upload was acknowledged, so there is nothing left to resend: finish with silence
      // where the backend is missing audio rather than leave the session stuck recording.
      console.warn('Iris is missing audio chunks; they become silence', err.missing)
      await endLiveSession(this.sessionId, { ...request, allowGaps: true }, this.env.fetch)
    }
    this.finished = true
    this.env.page.removeEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.removeEventListener('pagehide', this.onPageHide)
  }

  /** Stop devices, timers and the recorder. Uploads already queued carry on. */
  private release() {
    this.stopFrames?.()
    this.stopFrames = undefined
    if (this.recorder && this.recorder.state !== 'inactive') {
      this.recorder.stop()
    }
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    this.streams = []
    this.mixer?.close()
    this.mixer = undefined
  }

  /**
   * The page is going away during a prompt. Fail the start, whose cleanup discards the session
   * right away (microtasks run before the page unloads). A page restored from the back/forward
   * cache then can't record into the deleted session.
   */
  private onPageHideWhileStarting = () => this.cancel('Recording was cancelled before it started.')

  private discardSession() {
    const url = `/api/sessions/${this.sessionId}`
    void this.env.fetch(url, { method: 'DELETE', keepalive: true }).catch(() => {})
  }

  private onBeforeUnload = (e: Event) => {
    if (this.finished) return
    e.preventDefault()
    // Older browsers need returnValue set to show the "leave site?" prompt.
    ;(e as BeforeUnloadEvent).returnValue = ''
  }

  /** The page is going away anyway: end with what has arrived, rather than leave it recording. */
  private onPageHide = () => {
    if (this.finished) return
    const body = new Blob([JSON.stringify({ allow_gaps: true })], { type: 'application/json' })
    this.env.sendBeacon(`/api/live/${this.sessionId}/end`, body)
  }
}

/** Waits for `p`, but no longer than `ms`. */
async function settledWithin(p: Promise<unknown> | undefined, ms: number): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise((resolve) => (timer = setTimeout(resolve, ms)))
  try {
    await Promise.race([p, timeout])
  } finally {
    clearTimeout(timer)
  }
}

async function getDisplay(devices: Pick<MediaDevices, 'getDisplayMedia'>): Promise<MediaStream> {
  try {
    return await devices.getDisplayMedia({ video: true, audio: true })
  } catch (err) {
    // Browsers that can't share audio may reject the request (before showing a picker).
    const name = err instanceof Error || err instanceof DOMException ? err.name : ''
    if (name !== 'TypeError' && name !== 'NotSupportedError') {
      throw new CaptureError(describeCaptureError(err, 'screen'), { cause: err })
    }
  }
  try {
    return await devices.getDisplayMedia({ video: true })
  } catch (err) {
    throw new CaptureError(describeCaptureError(err, 'screen'), { cause: err })
  }
}

// --- Browser implementations of CaptureEnv ---------------------------------------------------

function mixAudio(streams: MediaStream[]): { stream: MediaStream; close(): void } {
  const ctx = new AudioContext()
  void ctx.resume()
  const dest = ctx.createMediaStreamDestination()
  for (const s of streams) ctx.createMediaStreamSource(s).connect(dest)
  return { stream: dest.stream, close: () => void ctx.close().catch(() => {}) }
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
      // No frame this tick (e.g. the shared window is minimised); try again next tick.
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

/**
 * Grabs frames from the shared track: with ImageCapture where available (Chrome, Edge), which
 * keeps working while this tab is in the background, else from a video element.
 */
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

/**
 * Calls `tick` every `intervalMs`. The timer runs in a worker: the user is usually looking at
 * what they share, so this tab is in the background, where page timers are heavily throttled.
 */
function startTicker(intervalMs: number, tick: () => void): () => void {
  try {
    const source = `setInterval(() => postMessage(0), ${intervalMs})`
    const url = URL.createObjectURL(new Blob([source], { type: 'text/javascript' }))
    const worker = new Worker(url)
    worker.onmessage = () => tick()
    return () => {
      worker.terminate()
      URL.revokeObjectURL(url)
    }
  } catch {
    const id = window.setInterval(tick, intervalMs)
    return () => window.clearInterval(id)
  }
}
