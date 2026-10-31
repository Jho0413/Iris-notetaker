// Browser live capture: mic (plus tab/system audio if shared) and a screen share.
// Audio goes up as MediaRecorder chunks every few seconds; screen frames as JPEGs at a low fixed
// rate. Change detection happens in the backend, not here (architecture section 1, live).

const AUDIO_CHUNK_MS = 5000
const FRAME_INTERVAL_MS = 1000
const JPEG_QUALITY = 0.8

export class LiveCapture {
  private recorder?: MediaRecorder
  private streams: MediaStream[] = []
  private audioCtx?: AudioContext
  private frameTimer?: number
  private startedAt = 0
  private seq = 0
  private pending = new Set<Promise<unknown>>()
  private readonly sessionId: string

  constructor(sessionId: string) {
    this.sessionId = sessionId
  }

  async start(): Promise<MediaStream> {
    const display = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
    const mic = await navigator.mediaDevices.getUserMedia({ audio: true })
    this.streams = [display, mic]

    // Mix mic and shared audio into one track.
    this.audioCtx = new AudioContext()
    const dest = this.audioCtx.createMediaStreamDestination()
    for (const s of this.streams) {
      if (s.getAudioTracks().length) this.audioCtx.createMediaStreamSource(s).connect(dest)
    }

    this.startedAt = performance.now()
    this.recorder = new MediaRecorder(dest.stream, { mimeType: 'audio/webm' })
    this.recorder.ondataavailable = (e) => {
      if (e.data.size) this.track(this.post(`audio?seq=${this.seq++}`, e.data))
    }
    this.recorder.start(AUDIO_CHUNK_MS)

    this.startFrames(display)
    return display
  }

  /** Stop capture, flush remaining uploads, then tell the backend to process the session. */
  async stop(): Promise<void> {
    window.clearInterval(this.frameTimer)
    if (this.recorder && this.recorder.state !== 'inactive') {
      const stopped = new Promise((resolve) => (this.recorder!.onstop = resolve))
      this.recorder.stop()
      await stopped
    }
    this.streams.forEach((s) => s.getTracks().forEach((t) => t.stop()))
    await this.audioCtx?.close()
    await Promise.all(this.pending)
    await fetch(`/api/live/${this.sessionId}/end`, { method: 'POST' })
  }

  private startFrames(display: MediaStream) {
    const video = document.createElement('video')
    video.srcObject = display
    video.muted = true
    void video.play()
    const canvas = document.createElement('canvas')

    this.frameTimer = window.setInterval(() => {
      if (!video.videoWidth) return
      const t = (performance.now() - this.startedAt) / 1000
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
      canvas.getContext('2d')!.drawImage(video, 0, 0)
      canvas.toBlob(
        (blob) => blob && this.track(this.post(`frame?t=${t.toFixed(3)}`, blob)),
        'image/jpeg',
        JPEG_QUALITY,
      )
    }, FRAME_INTERVAL_MS)
  }

  private post(path: string, body: Blob): Promise<Response> {
    return fetch(`/api/live/${this.sessionId}/${path}`, { method: 'POST', body })
  }

  private track(p: Promise<unknown>) {
    this.pending.add(p)
    void p.finally(() => this.pending.delete(p))
  }
}
