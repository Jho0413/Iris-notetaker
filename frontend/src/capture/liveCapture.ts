// Browser live capture: mic (plus tab/system audio if shared) and a screen share.
// Audio goes up as MediaRecorder chunks every few seconds; screen frames as JPEGs at a low fixed
// rate, timed on the audio's clock. Change detection happens in the backend, not here
// (architecture section 1, live). Uploads are retried until they land (uploadQueue.ts), and
// ending the session waits for all of them, so a dropped request doesn't lose content.

import { CaptureError, describeCaptureError, pickAudioMimeType } from './captureErrors'
import { EndSessionError, endLiveSession } from './endSession'
import { UploadQueue } from './uploadQueue'

const AUDIO_CHUNK_MS = 5000
const FRAME_INTERVAL_MS = 1000
const FRAME_MAX_WIDTH = 1920 // the backend scales further (IRIS_FRAME_MAX_WIDTH)
const JPEG_QUALITY = 0.8
const DRAIN_TIMEOUT_MS = 30_000

export type EndReason = 'screen-share-ended' | 'microphone-ended' | 'recorder-error'

export interface LiveCaptureOptions {
  /**
   * Capture ended on its own: the user stopped sharing, the mic went away, or the recorder
   * failed. Call `stop()` to finish the session. Without a handler, capture stops itself.
   */
  onEnded?: (reason: EndReason, message: string) => void
}

interface FrameGrabber {
  grab(canvas: HTMLCanvasElement): Promise<Blob | null>
  close(): void
}

export class LiveCapture {
  readonly sessionId: string
  private readonly options: LiveCaptureOptions
  private readonly queue: UploadQueue
  private recorder?: MediaRecorder
  private streams: MediaStream[] = []
  private audioCtx?: AudioContext
  private grabber?: FrameGrabber
  private stopTicker?: () => void
  private startedAt = 0
  private seq = 0
  private recording = false
  private finished = false
  private stopping?: Promise<void>

  constructor(sessionId: string, options: LiveCaptureOptions = {}) {
    this.sessionId = sessionId
    this.options = options
    this.queue = new UploadQueue(async (path, body) => {
      const res = await fetch(`/api/live/${sessionId}/${path}`, { method: 'POST', body })
      return res.status
    })
  }

  /** Uploads not yet acknowledged by the backend. */
  get pendingUploads(): number {
    return this.queue.pending
  }

  /**
   * Ask for the screen and the mic, then start recording. On failure (permission denied, the
   * picker cancelled, an unsupported browser) everything acquired is released, the empty
   * session is deleted, and a `CaptureError` with a user-facing message is thrown.
   */
  async start(): Promise<MediaStream> {
    try {
      return await this.open()
    } catch (err) {
      this.release()
      void fetch(`/api/sessions/${this.sessionId}`, { method: 'DELETE' }).catch(() => {})
      if (err instanceof CaptureError) throw err
      throw new CaptureError(`Could not start recording: ${err}`, { cause: err })
    }
  }

  /**
   * Stop capture, upload everything still pending, then tell the backend to process the
   * session. Throws if uploads or the end request fail; nothing captured is discarded, so
   * calling `stop()` again retries.
   */
  stop(): Promise<void> {
    this.stopping ??= this.finish().catch((err: unknown) => {
      this.stopping = undefined
      throw err
    })
    return this.stopping
  }

  private async open(): Promise<MediaStream> {
    const devices = navigator.mediaDevices
    if (!devices?.getDisplayMedia || typeof MediaRecorder === 'undefined') {
      throw new CaptureError(
        "This browser can't record the screen. Use a recent desktop Chrome, Edge or Firefox.",
      )
    }
    const mimeType = pickAudioMimeType((type) => MediaRecorder.isTypeSupported(type))
    if (!mimeType) {
      throw new CaptureError(
        "This browser can't record WebM audio. Use a recent desktop Chrome, Edge or Firefox.",
      )
    }

    const display = await getDisplay(devices)
    this.streams.push(display)
    let mic: MediaStream
    try {
      mic = await devices.getUserMedia({ audio: true })
    } catch (err) {
      throw new CaptureError(describeCaptureError(err, 'microphone'), { cause: err })
    }
    this.streams.push(mic)

    const recorder = new MediaRecorder(this.audioToRecord(display, mic), { mimeType })
    recorder.ondataavailable = (e) => {
      if (e.data.size) this.queue.add('audio', `audio?seq=${this.seq++}`, e.data)
    }
    recorder.onerror = () => this.ended('recorder-error', 'Recording stopped: the browser failed.')
    const started = new Promise((resolve) => recorder.addEventListener('start', resolve, { once: true }))
    recorder.start(AUDIO_CHUNK_MS)
    this.recorder = recorder
    await started
    // The audio's time 0: WebM timestamps start at the first recorded sample.
    this.startedAt = performance.now()
    this.recording = true

    display.getVideoTracks()[0]?.addEventListener('ended', () =>
      this.ended('screen-share-ended', 'Screen sharing stopped, so the recording was finished.'),
    )
    mic.getAudioTracks()[0]?.addEventListener('ended', () =>
      this.ended('microphone-ended', 'The microphone was disconnected, so the recording was finished.'),
    )
    window.addEventListener('beforeunload', this.onBeforeUnload)
    window.addEventListener('pagehide', this.onPageHide)
    this.startFrames(display)
    return display
  }

  /** The mic alone, or the mic mixed with the shared tab/system audio into one track. */
  private audioToRecord(display: MediaStream, mic: MediaStream): MediaStream {
    if (!display.getAudioTracks().length) return mic
    this.audioCtx = new AudioContext()
    void this.audioCtx.resume()
    const dest = this.audioCtx.createMediaStreamDestination()
    for (const s of [display, mic]) this.audioCtx.createMediaStreamSource(s).connect(dest)
    return dest.stream
  }

  private startFrames(display: MediaStream) {
    const track = display.getVideoTracks()[0]
    if (!track) return
    const grabber = frameGrabber(track)
    this.grabber = grabber
    const canvas = document.createElement('canvas')
    let busy = false
    this.stopTicker = startTicker(FRAME_INTERVAL_MS, async () => {
      if (busy || !this.recording) return
      busy = true
      try {
        const t = (performance.now() - this.startedAt) / 1000
        const blob = await grabber.grab(canvas)
        if (blob && this.recording) this.queue.add('frame', `frame?t=${t.toFixed(3)}`, blob)
      } catch {
        // No frame this tick (e.g. the shared window is minimised); try again next tick.
      } finally {
        busy = false
      }
    })
  }

  private ended(reason: EndReason, message: string) {
    if (!this.recording || this.stopping) return
    if (this.options.onEnded) this.options.onEnded(reason, message)
    else void this.stop().catch(() => {})
  }

  private async finish(): Promise<void> {
    if (this.recording) {
      this.recording = false
      this.stopTicker?.()
      const recorder = this.recorder
      if (recorder && recorder.state !== 'inactive') {
        const stopped = new Promise((resolve) => recorder.addEventListener('stop', resolve, { once: true }))
        recorder.stop() // emits the last chunk, then 'stop'
        await stopped
      }
      this.release()
    }
    const pending = await this.queue.drain(DRAIN_TIMEOUT_MS)
    if (pending) {
      throw new EndSessionError(
        `${pending} uploads haven't reached Iris yet. Check the backend is running, then try again.`,
      )
    }
    const rejectedAudio = this.queue.rejected.filter((r) => r.kind === 'audio')
    if (rejectedAudio.length) console.warn('Iris rejected audio chunks; they become silence', rejectedAudio)
    await endLiveSession(this.sessionId, {
      audioChunks: this.seq,
      allowGaps: rejectedAudio.length > 0,
    })
    this.finished = true
    window.removeEventListener('beforeunload', this.onBeforeUnload)
    window.removeEventListener('pagehide', this.onPageHide)
  }

  /** Stop devices and timers. Uploads already queued carry on. */
  private release() {
    this.stopTicker?.()
    this.stopTicker = undefined
    this.grabber?.close()
    this.grabber = undefined
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    this.streams = []
    void this.audioCtx?.close().catch(() => {})
    this.audioCtx = undefined
  }

  private onBeforeUnload = (e: BeforeUnloadEvent) => {
    if (this.finished) return
    e.preventDefault()
    e.returnValue = '' // older browsers need this to show the "leave site?" prompt
  }

  /** The page is going away anyway: end with what has arrived, rather than leave it recording. */
  private onPageHide = () => {
    if (this.finished) return
    const body = new Blob([JSON.stringify({ allow_gaps: true })], { type: 'application/json' })
    navigator.sendBeacon(`/api/live/${this.sessionId}/end`, body)
  }
}

async function getDisplay(devices: MediaDevices): Promise<MediaStream> {
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
