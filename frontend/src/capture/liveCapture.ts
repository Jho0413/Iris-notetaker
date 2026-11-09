// Browser live capture: the mic and, in the desktop app, the Mac's sound, plus a screen share that
// can be turned on and off at any time (in a browser, with its tab's sound if shared). The audio
// sources are mixed into one track, which goes up as MediaRecorder chunks every few seconds; screen
// frames as JPEGs at a low fixed rate while the screen is on, timed on the audio's clock and
// numbered by the share they belong to, so the backend finds where each share starts and ends from
// the frames alone. Change detection happens in the backend, not here. Uploads are retried
// through outages and busy answers (uploadQueue.ts; one the backend refuses outright is dropped),
// and finishing the recording (making notes, or keeping it without) waits for all of them, so a
// dropped request doesn't lose content.

import { engineDidNotAnswer } from '../session/request.ts'
import { CaptureError, describeCaptureError, inDesktopApp, pickAudioMimeType } from './captureErrors.ts'
import { EndSessionError, MissingAudioError, endLiveSession, stopLiveSession } from './endSession.ts'
import { UploadQueue, type UploadQueueOptions } from './uploadQueue.ts'

const AUDIO_CHUNK_MS = 5000
const FRAME_INTERVAL_MS = 1000
const FRAME_MAX_WIDTH = 1920 // the backend scales further (IRIS_FRAME_MAX_WIDTH)
const JPEG_QUALITY = 0.8
const DRAIN_TIMEOUT_MS = 30_000
const UPLOAD_TIMEOUT_MS = 30_000 // a hung request becomes a retry instead of blocking the queue
const RECORDER_STOP_TIMEOUT_MS = 2000 // a recorder that never reports 'stop' doesn't block ending

/** How a recording starts: audio alone, or with a screen share (which can be turned off later). */
export type CaptureMode = 'audio' | 'screen'

export type EndReason = 'microphone-ended' | 'recorder-error'

/** The request that finishes a recording: `end` makes notes, `stop` keeps it without notes. */
type FinishRequest = 'end' | 'stop'

/** Why `start()` failed when capture ended before recording began. */
const START_FAILED: Record<EndReason | 'screen-share-ended', string> = {
  'screen-share-ended': 'Screen sharing stopped before recording started. Share again to start.',
  'microphone-ended': 'The microphone was disconnected before recording started. Try again.',
  'recorder-error': 'The browser could not start recording. Try again.',
}

export interface LiveCaptureOptions {
  /**
   * Capture ended on its own: the mic went away, or the recorder failed. Call `stop()` to finish
   * the session. Without a handler, capture stops itself.
   */
  onEnded?: (reason: EndReason, message: string) => void
  /**
   * The screen share ended on its own (the browser's or macOS's Stop Sharing, or the shared
   * window closed): the screen is off and the recording carries on with the audio.
   */
  onScreenEnded?: () => void
  /**
   * The Mac's sound stopped during a recording (macOS refused it after asking): the recording
   * carries on with the mic.
   */
  onMacSoundEnded?: () => void
  /** How long `stop()` and `keep()` wait for pending uploads before giving up (they can be called again). */
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
  /**
   * The desktop app answers a request for sound alone with the Mac's sound and no picker
   * (desktop/main.cjs), so it is recorded whether or not the screen is shared, and a screen share
   * asks for the picture alone. A browser can't record it without a share.
   */
  macSound: boolean
  /** A mixer: the one audio track that is recorded, which sources join and leave. */
  mixAudio: () => AudioMixer
  /** Grab frames from `track` about once a second; returns a function that stops. */
  captureFrames: (track: MediaStreamTrack, onFrame: (jpeg: Blob, at: number) => void) => () => void
}

export interface AudioMixer {
  /** The mixed track, recorded for the whole recording. */
  stream: MediaStream
  /** Mix in `source` (it has an audio track); returns a function that takes it out again. */
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
    macSound: inDesktopApp(),
    mixAudio,
    captureFrames,
  }
}

/**
 * macOS refused the Mac's sound to a recording in the desktop app, so only the mic is recorded:
 * the Live page says how to allow it. A browser never records it, so nothing is refused.
 */
export function macSoundRefused(desktopApp: boolean, started: Pick<StartedRecording, 'macSound'>): boolean {
  return desktopApp && !started.macSound
}

/**
 * The recording's clock, which leaves stopped time out: what was recorded before (`recordedMs`)
 * plus the time since capture last (re)started (`since`, ms since the epoch; 0 while stopped).
 */
export interface RecordingClock {
  recordedMs: number
  since: number
}

/** How long has been recorded at `now`. */
export function recordedMs(clock: RecordingClock, now: number): number {
  return clock.recordedMs + (clock.since ? Math.max(0, now - clock.since) : 0)
}

/** The clock stopped at `now`: what was recorded is kept, nothing more counts. */
export function pausedClock(clock: RecordingClock, now: number): RecordingClock {
  return { recordedMs: recordedMs(clock, now), since: 0 }
}

/**
 * When the clock would have started had it never stopped: what the menu bar is told, so its
 * elapsed time (now minus this) matches the page's.
 */
export function clockStart(clock: RecordingClock): number {
  return clock.since - clock.recordedMs
}

/** What a started recording captures, for the Live page to show. */
export interface StartedRecording {
  /** The shared screen, for its preview; null while the screen is off. */
  screen: MediaStream | null
  /**
   * The Mac's sound is mixed in: a recording in the desktop app that macOS allowed. False when it
   * was refused, and in a browser, which records the mic alone (or with a shared tab's sound).
   */
  macSound: boolean
}

export class LiveCapture {
  /** Made once every permission is granted (`start`): until then, nothing is being recorded. */
  private id?: string
  private readonly createSession: () => Promise<string>
  private readonly options: LiveCaptureOptions
  private readonly env: CaptureEnv
  private readonly queue: UploadQueue
  private recorder?: MediaRecorder
  /** Settles on the recorder's 'stop' event, which follows its last chunk. */
  private recorderStopped?: Promise<unknown>
  /** The sources being captured now, each with what takes it out of the mixer. */
  private sources: { stream: MediaStream; unmix?: () => void }[] = []
  private mixer?: AudioMixer
  /** The shared screen while the screen is on (also one of `sources`). */
  private display?: MediaStream
  private stopFrames?: () => void
  /** The screen was on when the recording was stopped (`pause`): `resume` turns it on again. */
  private screenAtPause = false
  /**
   * Shares so far: each frame carries its share's number, so the backend finds the shares from the
   * frames alone.
   */
  private shares = 0
  private sharing?: Promise<MediaStream | null>
  private startedAt = 0
  /** Time spent stopped (`pause`), which the recording's clock leaves out. */
  private pausedMs = 0
  private pausedAt = 0
  private seq = 0
  /** Started and not yet finished or discarded, whether capturing or stopped. */
  private recording = false
  private paused = false
  private resuming?: Promise<StartedRecording>
  private finished = false
  private startFailed = false
  private sessionDiscarded = false
  private opening?: Promise<StartedRecording>
  private cancelled = false
  private cancelOpening?: (err: CaptureError) => void
  /** The finish under way or done (`stop` or `keep`), and the request it ends with. */
  private stopping?: Promise<void>
  private stoppingWith?: FinishRequest
  /** Make notes was asked for (`stop`): from then on, finishing makes them, even after a failure. */
  private notesChosen = false

  /**
   * `createSession` makes the session to record into (`POST /live`) and resolves to its id, or
   * throws a `CaptureError` saying why it can't. `start` calls it only once what it records has
   * been granted, so asking for permissions never leaves a session behind.
   */
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

  /** The session being recorded into, once `start` has made it. */
  get sessionId(): string | undefined {
    return this.id
  }

  /** Uploads not yet acknowledged by the backend. */
  get pendingUploads(): number {
    return this.queue.pending
  }

  /**
   * Ask for what `mode` records, then make the session and start recording: the screen (for
   * `screen`), the mic and, in the desktop app, the Mac's sound (if macOS refuses it, the
   * recording starts without it). On failure (permission denied, the picker cancelled, an
   * unsupported browser, the session not made, sharing stopped or the recorder failing before
   * recording began, or `stop()` called meanwhile) everything acquired is released at once, a
   * session already made is deleted, and a `CaptureError` with a user-facing message is thrown.
   * A failure while a permission prompt is still open doesn't wait for the prompt: a stream
   * granted afterwards is stopped as soon as it arrives, and so is a session made afterwards.
   */
  async start(mode: CaptureMode): Promise<StartedRecording> {
    const cancelled = new Promise<never>((_, reject) => (this.cancelOpening = reject))
    this.opening = Promise.race([this.open(mode), cancelled])
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
   * Make notes: stop capture, upload everything still pending, then tell the backend to process
   * the session. Throws if uploads or the end request fail; nothing captured is discarded, so
   * calling `stop()` again retries. Called while `start()` is still asking for permissions, it
   * cancels the start instead (nothing was recorded, so the session is discarded). After a
   * failed start there is nothing to finish, so it resolves without contacting the backend.
   * After `keep`, it makes notes from the kept recording once the keep has settled.
   */
  stop(): Promise<void> {
    this.notesChosen = true
    return this.finishWith('end')
  }

  /**
   * Keep the recording without making notes (leaving the Live page): as `stop`, but the backend
   * is told only to stop recording, so the library offers Make notes or Delete. Throws, and is
   * retried, the same way. Once Make notes was chosen it joins that instead (or retries it, if
   * it failed): keeping never undoes Make notes.
   */
  keep(): Promise<void> {
    return this.finishWith(this.notesChosen ? 'end' : 'stop')
  }

  /** One finish at a time: a call joins the one under way (or done), unless it makes notes after a keep. */
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
      // Cancelled while it was being made: start()'s cleanup may have run before the id was known.
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
    // The audio's time 0: WebM timestamps start at the first recorded sample.
    this.startedAt = performance.now()
    if (got.screen) this.captureScreen(got.screen)
    // Nothing below can fail, so a start that threw never looks like a recording.
    this.env.page.addEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.addEventListener('pagehide', this.onPageHide)
    this.recording = true
    return this.started(got)
  }

  /**
   * Stop capturing, keeping the recording: the recorder pauses (its clock too, so the stopped
   * time isn't in the recording, measured in Electron 45), frames stop, and the microphone, the
   * Mac's sound and any shared screen are released (the screen is turned off). `resume` carries
   * on in the same recording; `stop` makes notes from it; `keep` keeps it without notes; `discard`
   * throws it away.
   */
  pause(): void {
    if (!this.recording || this.paused || this.stopping || this.resuming) return
    this.paused = true
    this.pausedAt = performance.now()
    this.screenAtPause = Boolean(this.display)
    if (this.recorder?.state === 'recording') {
      // Send what the recorder holds now: if the page goes away while stopped, it is uploaded.
      this.recorder.requestData()
      this.recorder.pause()
    }
    this.stopFrames?.()
    this.stopFrames = undefined
    this.display = undefined
    this.releaseSources()
  }

  /**
   * Capture again after `pause`: ask for the mic and the Mac's sound again and, if the screen was
   * on when it stopped, a screen (the picker opens again; cancelling it carries on with the screen
   * off), and carry on in the same recording. If that fails (the mic blocked) it throws a
   * `CaptureError` and the recording stays stopped; nothing is lost.
   */
  resume(): Promise<StartedRecording> {
    if (!this.paused || this.stopping) return Promise.reject(new CaptureError('The recording is not stopped.'))
    this.resuming ??= (async () => {
      try {
        const got = await this.acquire(this.screenAtPause, { screenOptional: true })
        if (!this.paused || this.stopping) throw new CaptureError('The recording was finished meanwhile.')
        // Ended while the next prompt was open: their 'ended' was ignored while stopped.
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
        if (got.screen) this.captureScreen(got.screen) // a new share: what follows Stop is new
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

  /**
   * Turn the screen on during a recording: the picker opens (in a browser, its dialog), and the
   * share's frames are sent from now on, as a new share. In a browser the share's
   * tab sound is mixed in; in the app the picture alone is asked for, since the Mac's sound is
   * recorded throughout. Throws a `CaptureError` if nothing is shared (the picker cancelled,
   * sharing blocked); the recording carries on. Resolves to null if the recording was stopped
   * meanwhile (the app closes its picker then): nothing is shared, and there is nothing to say.
   */
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

  /** Turn the screen off during a recording: the share is released and the audio carries on. */
  stopScreen(): void {
    if (!this.display || !this.recording || this.paused || this.stopping) return
    this.releaseDisplay()
  }

  /**
   * Throw the recording away: delete the session, then stop everything and send nothing more.
   * The delete goes first: if it fails, this throws and the recording is untouched (still
   * stopped, or recording), so it can be continued, finished, or discarded again.
   */
  async discard(): Promise<void> {
    if (this.opening) {
      // start() fails now, releasing what it has and discarding the session itself.
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
    // 404: already gone (a delete that arrived, or the session was removed elsewhere).
    if (!res.ok && res.status !== 404) {
      throw new CaptureError("Iris couldn't delete the recording. Try again.")
    }
    // The session is gone, so the backend refuses anything sent from here on anyway.
    this.recording = false
    this.paused = false
    this.finished = true
    this.queue.close()
    if (this.recorder) this.recorder.ondataavailable = null
    this.release()
    this.env.page.removeEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.removeEventListener('pagehide', this.onPageHide)
  }

  /**
   * The sources to record, acquired and listened to: the screen (if `screen`), the mic and, in the
   * desktop app, the Mac's sound (if macOS refuses it, there is none). Each is kept for release
   * (`this.sources`), or stopped at once if the start was cancelled meanwhile. With
   * `screenOptional`, a screen that isn't shared (the picker cancelled) leaves the screen off.
   */
  private async acquire(
    screen: boolean,
    { screenOptional = false } = {},
  ): Promise<{ screen: MediaStream | null; mic: MediaStream; sound: MediaStream | null }> {
    const devices = this.env.mediaDevices!
    let display: MediaStream | null = null
    if (screen) {
      try {
        display = this.acquired(await getDisplay(devices, !this.env.macSound))
      } catch (err) {
        if (!screenOptional || !(err instanceof CaptureError) || this.cancelled || this.stopping) throw err
        console.warn('The screen was not shared again; continuing with the screen off', err)
      }
    }
    // Listen from the start: sharing stopped during the mic prompt must fail the start too.
    if (display) this.listenForScreenEnd(display)
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
    const sound = this.env.macSound ? await getMacSound(devices) : null
    if (sound) this.acquired(sound) // stopped at once if cancelled meanwhile
    this.throwIfCancelled() // cancelled while a request that then failed was open
    sound?.getAudioTracks()[0]?.addEventListener('ended', this.onMacSoundEnded)
    return { screen: display, mic, sound }
  }

  /** Mix every source with live audio that isn't mixed in yet into the recorded track. */
  private mixIn() {
    for (const source of this.sources) {
      if (!source.unmix && hasLiveAudio(source.stream)) source.unmix = this.mixer!.add(source.stream)
    }
  }

  /**
   * Send the shared screen's frames as a new share, timed on the recording's clock (stopped time
   * left out).
   */
  private captureScreen(display: MediaStream) {
    const track = display.getVideoTracks()[0]
    if (!track) return
    this.display = display
    const share = ++this.shares
    this.stopFrames = this.env.captureFrames(track, (jpeg, at) => {
      // A frame still being grabbed when its share was turned off is dropped: none come after.
      if (!this.recording || this.paused || this.display !== display) return
      this.queue.add('frame', `frame?t=${this.recordedSeconds(at).toFixed(3)}&share=${share}`, jpeg)
    })
  }

  /** Seconds recorded at `at` (a `performance.now()` time): the session's clock. */
  private recordedSeconds(at: number): number {
    return (at - this.startedAt - this.pausedMs) / 1000
  }

  /**
   * The share ending on its own: before recording starts it fails the start; while recording
   * the screen turns off and the recording carries on; while stopped, nothing (it was let go).
   */
  private listenForScreenEnd(display: MediaStream) {
    display.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (this.opening && !this.recording) return this.cancel(START_FAILED['screen-share-ended'])
      if (this.display !== display || this.paused || this.stopping) return
      this.releaseDisplay()
      this.options.onScreenEnded?.()
    })
  }

  /** Stop the shared screen and its frames, and take its sound out of the mixer. */
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

  /** What the Live page shows: the screen, and whether the Mac's sound is mixed in. */
  private started(got: { screen: MediaStream | null; sound: MediaStream | null }): StartedRecording {
    // The Mac's sound ending while the recorder started (macOS answered its prompt meanwhile).
    return { screen: got.screen, macSound: got.sound ? hasLiveAudio(got.sound) : false }
  }

  private throwIfCancelled() {
    if (this.cancelled) throw new CaptureError('Recording was cancelled before it started.')
  }

  /** Keep `stream` for release, or stop it at once if the start was cancelled meanwhile, or the
   * recording finished while `resume` was asking for it. */
  private acquired(stream: MediaStream): MediaStream {
    if (this.cancelled || (this.resuming && (this.stopping || !this.recording))) {
      stream.getTracks().forEach((t) => t.stop())
      this.throwIfCancelled()
      throw new CaptureError('The recording was finished meanwhile.')
    }
    this.sources.push({ stream })
    return stream
  }

  /** Fail a start in progress now, without waiting for whatever it is awaiting (a prompt). */
  private cancel(message: string) {
    this.cancelled = true
    this.cancelOpening?.(new CaptureError(message))
  }

  /** The Mac's sound stopped mid-recording: the mixer goes on with the mic. */
  private onMacSoundEnded = () => {
    if (this.recording && !this.paused && !this.stopping) this.options.onMacSoundEnded?.()
  }

  private ended(reason: EndReason, message: string) {
    if (this.opening && !this.recording) return this.cancel(START_FAILED[reason])
    // While stopped, or continuing (its sources are not recorded yet), nothing is lost.
    if (!this.recording || this.paused || this.stopping) return
    if (this.options.onEnded) this.options.onEnded(reason, message)
    else void this.stop().catch(() => {})
  }

  /** Stop capturing, upload what is left, then send `request`: `end` makes notes, `stop` keeps it. */
  private async finish(request: FinishRequest): Promise<void> {
    if (this.opening) {
      // start() fails now, releasing what it has and discarding the session.
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
    if (request === 'stop') {
      await stopLiveSession(this.id!, this.env.fetch)
    } else {
      const end = { audioChunks: this.seq, allowGaps: rejectedAudio.length > 0 }
      try {
        await endLiveSession(this.id!, end, this.env.fetch)
      } catch (err) {
        if (!(err instanceof MissingAudioError)) throw err
        // Every upload was acknowledged, so there is nothing left to resend: finish with silence
        // where the backend is missing audio rather than leave the session stuck recording.
        console.warn('Iris is missing audio chunks; they become silence', err.missing)
        await endLiveSession(this.id!, { ...end, allowGaps: true }, this.env.fetch)
      }
    }
    this.finished = true
    this.env.page.removeEventListener('beforeunload', this.onBeforeUnload)
    this.env.page.removeEventListener('pagehide', this.onPageHide)
  }

  /** Stop devices, timers and the recorder. Uploads already queued carry on. */
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

  /** Stop the sources being captured and take them out of the mixer; the recorder carries on. */
  private releaseSources() {
    for (const { stream, unmix } of this.sources) {
      unmix?.()
      stream.getTracks().forEach((t) => t.stop())
    }
    this.sources = []
  }

  /**
   * The page is going away during a prompt. Fail the start, whose cleanup discards a session made
   * meanwhile right away (microtasks run before the page unloads). A page restored from the
   * back/forward cache then can't record into the deleted session.
   */
  private onPageHideWhileStarting = () => this.cancel('Recording was cancelled before it started.')

  /** Delete a session made by a start that then failed (once; before one is made there is none). */
  private discardSession() {
    if (!this.id || this.sessionDiscarded) return
    this.sessionDiscarded = true
    const url = `/api/sessions/${this.id}`
    void this.env.fetch(url, { method: 'DELETE', keepalive: true }).catch(() => {})
  }

  private onBeforeUnload = (e: Event) => {
    if (this.finished) return
    e.preventDefault()
    // Older browsers need returnValue set to show the "leave site?" prompt.
    ;(e as BeforeUnloadEvent).returnValue = ''
  }

  /**
   * The page is going away anyway (the tab closed, a reload, the app quitting), so nothing more
   * will be captured. A recording capturing, stopped, or being kept (`keep()`: its uploads still
   * finishing, or its stop request failed) is stopped without notes, so leaving never makes notes
   * nobody asked for: the library offers Make notes or Delete. Once Make notes was chosen
   * (`stop()`, likewise), it ends the session with what has arrived. Best effort: the desktop app
   * also stops its recordings itself.
   */
  private onPageHide = () => {
    if (this.finished) return
    // A body even for stop: the desktop app can't forward a request with an empty one.
    const [action, request] = this.notesChosen ? ['end', { allow_gaps: true }] : ['stop', {}]
    const body = new Blob([JSON.stringify(request)], { type: 'application/json' })
    this.env.sendBeacon(`/api/live/${this.id}/${action}`, body)
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

/**
 * Shared (system or tab) audio is a recording of what plays, not a voice in a room: echo
 * cancellation, noise suppression and gain control would only distort it. The microphone keeps
 * them (getUserMedia's defaults).
 */
export const SHARED_AUDIO: MediaTrackConstraints = {
  echoCancellation: false,
  noiseSuppression: false,
  autoGainControl: false,
}

/**
 * `navigator.sendBeacon`, or a keepalive fetch where beacons aren't allowed: they throw for
 * non-HTTP(S) pages such as the desktop app's app://iris. Both outlive the page.
 */
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

/** Whether a screen can be shared during a recording here (Share screen is offered). */
export function canShareScreen(devices: Pick<MediaDevices, 'getDisplayMedia'> | undefined): boolean {
  return typeof devices?.getDisplayMedia === 'function'
}

function hasLiveAudio(stream: MediaStream): boolean {
  return stream.getAudioTracks().some((t) => t.readyState === 'live')
}

/**
 * The Mac's sound alone, which the desktop app gives with no picker and no Screen Recording
 * permission. Null if it can't: a refusal (macOS's System Audio Recording) doesn't reject, it
 * gives a track that has already ended (measured, Electron 45 on macOS 26), which `hasLiveAudio`
 * catches.
 */
async function getMacSound(devices: Pick<MediaDevices, 'getDisplayMedia'>): Promise<MediaStream | null> {
  try {
    return await devices.getDisplayMedia({ video: false, audio: SHARED_AUDIO })
  } catch (err) {
    console.warn("The Mac's sound can't be recorded; recording the microphone alone", err)
    return null
  }
}

/**
 * A screen, window or tab to share, with its sound if `withSound` (a browser: a tab's sound is the
 * only sound it can record besides the mic). The desktop app asks for the picture alone: the Mac's
 * sound is recorded on its own throughout, so a share's sound would be heard twice.
 */
async function getDisplay(devices: Pick<MediaDevices, 'getDisplayMedia'>, withSound: boolean): Promise<MediaStream> {
  if (withSound) {
    try {
      return await devices.getDisplayMedia({ video: true, audio: SHARED_AUDIO })
    } catch (err) {
      // Browsers that can't share audio may reject the request (before showing a picker).
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

// --- Browser implementations of CaptureEnv ---------------------------------------------------

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

/** Start the timer worker ticking every `intervalMs`; returns a function that terminates it. May throw. */
export type StartWorker = (intervalMs: number, onMessage: () => void, onError: (event: Event) => void) => () => void

/**
 * Calls `tick` every `intervalMs`; returns a function that stops. The timer runs in a worker: the
 * user is usually looking at what they share, so this tab is in the background, where page timers
 * are heavily throttled. A worker that can't run falls back to a page timer, throttled but still
 * ticking. Such a worker can throw at once (no Worker support) or report an error event later: a
 * Content-Security-Policy block arrives that way (the desktop app blocked a blob: worker, #39),
 * so both fall back.
 */
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
    if (pageTimer !== undefined) terminateWorker() // it failed before startWorker returned
  } catch (err) {
    fallBack('worker could not start', err)
  }

  return () => {
    stopped = true
    terminateWorker?.()
    clearInterval(pageTimer)
  }
}

/**
 * The worker is a file of its own, which Vite builds into the app's assets, so it loads from the
 * app's own origin and no Content-Security-Policy exception is needed (Vite needs `new URL(...)`
 * written inside `new Worker(...)` to find it).
 */
const startTimerWorker: StartWorker = (intervalMs, onMessage, onError) => {
  const worker = new Worker(new URL('./frameTicker.worker.ts', import.meta.url), { type: 'module' })
  worker.onmessage = () => onMessage()
  worker.onerror = onError
  worker.postMessage(intervalMs)
  return () => worker.terminate()
}
