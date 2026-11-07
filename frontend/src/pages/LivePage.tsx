import { useEffect, useRef, useState } from 'react'
import { useBlocker, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { CaptureError, inDesktopApp } from '../capture/captureErrors'
import { EndSessionError } from '../capture/endSession'
import {
  type CaptureMode,
  clockStart,
  LiveCapture,
  macSoundRefused,
  pausedClock,
  type RecordingClock,
  recordedMs,
} from '../capture/liveCapture'
import { menuBar, type LiveLocationState } from '../capture/menuBar'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Icon } from '../components/Icon'
import { UNREACHABLE } from '../session/messages'
import { act } from '../session/request'
import { formatClock, liveProgress } from '../session/state'
import { useSessionStream } from '../session/useSessionStream'
import { useTitle } from '../session/useTitle'
import { irisApp, type RecordCommand } from '../settings/appBridge'

/**
 * `stopped`: capture is paused (Stop): make notes, continue, start over or discard; `continuing`:
 * asking for the sources again (Continue), still stopped until they are granted.
 * `stopping`: finishing the upload to make notes. `unfinished`: capture has ended, but telling the
 * backend to make notes failed; `finishing`: retrying that request.
 */
type Phase =
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'stopped'
  | 'continuing'
  | 'stopping'
  | 'unfinished'
  | 'finishing'

/** A choice on the stopped page that deletes what was recorded, so it is confirmed first. */
type Destructive = 'discard' | 'start-over'

/**
 * Plain words for a capture failure. Messages from the capture module are ours and shown as they
 * are; anything else (an unexpected error's text) never is.
 */
function captureError(err: unknown): string {
  if (err instanceof CaptureError) return err.message
  if (!(err instanceof DOMException)) return "Recording couldn't start. Try again."
  const name = err.name
  if (name === 'NotAllowedError') {
    return 'Screen sharing or the microphone was blocked or cancelled. Allow both for this site in your browser, then try again.'
  }
  if (name === 'NotFoundError') return 'No microphone was found. Connect one and try again.'
  if (name === 'NotReadableError') return 'The microphone or screen is in use by another app. Close it and try again.'
  return "Recording couldn't start. Try again."
}

/** What to say when finishing the recording failed: our own message, or a generic one. */
function finishError(err: unknown): string {
  return err instanceof EndSessionError ? err.message : "The recording couldn't be finished. Try again."
}

export function LivePage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [title, setTitle] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The recording's clock leaves stopped time out: what was recorded before, plus the time since
  // capture last (re)started (`since`, 0 while stopped).
  const [clock, setClock] = useState<RecordingClock>({ recordedMs: 0, since: 0 })
  const [now, setNow] = useState(0)
  const [discarding, setDiscarding] = useState(false)
  const [confirming, setConfirming] = useState<Destructive | null>(null)
  const [discarded, setDiscarded] = useState(false)
  const capture = useRef<LiveCapture | null>(null)
  const stopping = useRef(false)
  const [mode, setMode] = useState<CaptureMode>('screen')
  const [display, setDisplay] = useState<MediaStream | null>(null)
  const [macSound, setMacSound] = useState(false)
  const desktopApp = inDesktopApp()
  const recording = phase === 'recording'
  const continuing = phase === 'continuing'
  // The stopped page shows while asking to continue too, its buttons waiting.
  const stopped = phase === 'stopped' || continuing
  const busy = continuing || discarding
  // Started and not yet handed over (or thrown away): leaving the page would end it.
  const open = recording || stopped
  // Told to the menu bar while stopped too, so a refusal brings the window up once per recording.
  const soundRefused = open && macSoundRefused(desktopApp, mode, { macSound })
  const elapsed = recordedMs(clock, now) / 1000

  useTitle(recording ? `● Recording ${formatClock(elapsed)}` : stopped ? `Stopped ${formatClock(elapsed)}` : 'Record live')

  // What live processing has done so far, as one quiet line (not announced: it changes often).
  const stream = useSessionStream(open || phase === 'stopping' ? sessionId ?? undefined : undefined)
  const progress = liveProgress(stream.run)

  // Leaving ends the recording: the browser asks before closing the tab, the app before navigating.
  const blocker = useBlocker(open)
  useEffect(() => {
    if (!open) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [open])
  useEffect(() => {
    if (!recording) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [recording])

  // If the page goes away mid-recording anyway, end the session so it gets processed. During a
  // start, stopping cancels it: everything granted is released (and a session made meanwhile is
  // deleted).
  // Until that stop settles the app's menu bar says the upload is finishing, so quitting still asks
  // (capture/menuBar.ts: chunks not yet uploaded are only in this window's memory).
  useEffect(() => () => menuBar.left(capture.current?.stop()), [])

  async function start(mode: CaptureMode) {
    setError(null)
    setDiscarded(false)
    setMode(mode)
    setPhase('requesting')
    // Made only once what is recorded has been granted (LiveCapture.start): asking for
    // permissions leaves no session behind, whatever happens meanwhile.
    const createSession = async () => {
      const { data } = await api
        .POST('/api/live', { body: { title: title.trim() || 'Live session', screen: mode === 'screen' } })
        .catch(() => ({ data: undefined }))
      if (!data) throw new CaptureError(UNREACHABLE)
      return data.id
    }
    // Sharing stopped from the browser's own controls, the mic went away or the recorder failed:
    // finish. Held before start() so leaving the page during a prompt cancels the start.
    const live = new LiveCapture(createSession, {
      onEnded: () => void stopRef.current(),
      onMacSoundEnded: () => setMacSound(false),
    })
    capture.current = live
    try {
      const started = await live.start(mode)
      setDisplay(started.screen)
      setMacSound(started.macSound)
      setSessionId(live.sessionId ?? null)
      const t = Date.now()
      setClock({ recordedMs: 0, since: t })
      setNow(t)
      setPhase('recording')
    } catch (err) {
      // Nothing was captured, and the capture module has deleted any session it had made.
      capture.current = null
      setPhase('idle')
      setError(captureError(err))
    }
  }

  /**
   * Stop capture and hand over to processing. Stopping tears capture down, uploads what is still
   * queued, then tells the backend to finish. A failure leaves the recording stopped but
   * unfinished: the page then offers to retry (`finish`), which resends what is still queued and
   * the finish request through the same capture. Resolves to the finished session's id, or null.
   */
  async function stop(): Promise<string | null> {
    const live = capture.current
    if (!live || stopping.current) return null
    stopping.current = true
    setError(null)
    setPhase('stopping')
    try {
      await live.stop()
      capture.current = null
      return live.sessionId ?? null
    } catch (err) {
      // Kept, so finishing again retries the uploads still queued as well as the end request.
      setPhase('unfinished')
      setError(finishError(err))
      return null
    } finally {
      stopping.current = false
    }
  }

  async function finish() {
    if (!sessionId) return
    setPhase('finishing')
    setError(null)
    const live = capture.current
    if (live) {
      try {
        await live.stop()
        capture.current = null
        return navigate(`/sessions/${sessionId}`)
      } catch (err) {
        setPhase('unfinished')
        setError(finishError(err))
        return
      }
    }
    const result = await act(api.POST('/api/live/{session_id}/end', { params: { path: { session_id: sessionId } } }))
    // 409: the session is no longer recording, so an earlier finish request did arrive.
    if (result.ok || result.status === 409) return navigate(`/sessions/${sessionId}`)
    setPhase('unfinished')
    setError(result.status === 0 ? UNREACHABLE : "Iris couldn't finish the session. Try again in a moment.")
  }

  /** Stop: capture pauses and every device is let go; the stopped page offers what next. */
  function pause() {
    const live = capture.current
    if (!live || phase !== 'recording') return
    live.pause()
    const t = Date.now()
    setClock((c) => pausedClock(c, t))
    setNow(t)
    setError(null)
    setPhase('stopped')
  }

  /** Capture again in the same recording (with a screen, the picker opens again). */
  async function continueRecording() {
    const live = capture.current
    if (!live || phase !== 'stopped' || busy) return
    setPhase('continuing')
    setError(null)
    try {
      const again = await live.resume(mode)
      setDisplay(again.screen)
      setMacSound(again.macSound)
      const t = Date.now()
      setClock((c) => ({ ...c, since: t }))
      setNow(t)
      setPhase('recording')
    } catch (err) {
      setPhase('stopped') // still stopped: nothing recorded is lost
      setError(captureError(err))
    }
  }

  /**
   * Throw the recording away (its session is deleted); `again` starts a new one with the same
   * title and mode. A failed delete leaves the page stopped with the error, to try again.
   */
  async function discard(again: boolean) {
    const live = capture.current
    setConfirming(null)
    if (!live || busy) return
    setDiscarding(true)
    setError(null)
    try {
      await live.discard()
    } catch (err) {
      setError(captureError(err)) // not deleted: the recording is still there, stopped
      return
    } finally {
      setDiscarding(false)
    }
    capture.current = null
    setSessionId(null)
    setDisplay(null)
    setClock({ recordedMs: 0, since: 0 })
    setPhase('idle')
    if (again) void start(mode)
    else setDiscarded(true)
  }

  async function stopAndOpen() {
    // The id comes from the capture: `sessionId` state can lag if capture ends right after starting.
    const id = await stop()
    if (id) navigate(`/sessions/${id}`)
  }
  const stopRef = useRef(stopAndOpen)
  // The menu bar's commands for this page (capture/menuBar.ts), always the latest render's.
  const commandRef = useRef<(command: RecordCommand) => void>(() => {})
  /** Start, unless a recording is already under way (or stopped and not yet finished). */
  function startIfIdle(mode: CaptureMode) {
    if (phase === 'idle') void start(mode)
  }
  const startRef = useRef(startIfIdle)
  useEffect(() => {
    stopRef.current = stopAndOpen
    startRef.current = startIfIdle
    commandRef.current = (command) => {
      if (command === 'stop') pause()
      else if (command === 'continue') void continueRecording()
      else if (command === 'make-notes' && phase === 'stopped' && !busy) void stopAndOpen()
      else if ((command === 'start-over' || command === 'discard') && phase === 'stopped' && !busy) {
        setConfirming(command)
      }
    }
  })

  // The desktop app's menu bar icon (capture/menuBar.ts). It shows what this page is doing, and
  // its Record and Stop go through the same start() and stop as the buttons here.
  // While recording, the app is told when the clock would have started without the stopped time,
  // so its clock shows the same elapsed time as the page's.
  const reportedStart = recording ? clockStart(clock) : 0
  useEffect(() => {
    menuBar.phase(phase, reportedStart, soundRefused)
  }, [phase, reportedStart, soundRefused])
  useEffect(() => irisApp()?.onRecordCommand((command) => commandRef.current(command)), [])
  const startRequest = (location.state as LiveLocationState | null)?.startRequest
  const handledStart = useRef<number | undefined>(undefined)
  useEffect(() => {
    // Once per request: StrictMode runs effects twice, and a reload keeps the location state.
    if (!startRequest || handledStart.current === startRequest.id) return
    handledStart.current = startRequest.id
    navigate('.', { replace: true, state: null })
    startRef.current(startRequest.mode)
  }, [startRequest, navigate])

  if (phase === 'unfinished' || phase === 'finishing') {
    return (
      <div className="page-narrow">
        <div className="page-head">
          <div>
            <h1>Recording stopped</h1>
            <p className="lede">What was captured is saved on this computer.</p>
          </div>
        </div>
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <div className="banner-body">
            <p>
              <strong>Iris couldn't start making the notes.</strong> {error}
            </p>
            <div className="banner-actions">
              <button type="button" className="btn btn-primary" onClick={() => void finish()} disabled={phase === 'finishing'}>
                <Icon name="retry" /> {phase === 'finishing' ? 'Trying…' : 'Try again'}
              </button>
              {sessionId && (
                <button type="button" className="btn" onClick={() => navigate(`/sessions/${sessionId}`)}>
                  Open the session
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    )
  }

  if (phase === 'idle' || phase === 'requesting') {
    const requesting = phase === 'requesting'
    const choices: { mode: CaptureMode; icon: 'mic' | 'screen'; label: string; detail: string; waiting: string }[] = [
      {
        mode: 'audio',
        icon: 'mic',
        label: 'Record audio',
        detail: desktopApp
          ? "Your microphone and the Mac's sound, so both sides of a call."
          : 'Your microphone only. To include a call\'s sound, record the screen and share its tab.',
        waiting: 'Starting… Allow the microphone if asked.',
      },
      {
        mode: 'screen',
        icon: 'screen',
        label: 'Record audio + screen',
        detail: desktopApp
          ? 'Also a screen or window you choose, with its sound.'
          : 'Also a screen, window or tab you choose. Share a tab to include its sound.',
        waiting: desktopApp ? 'Choose what to record…' : 'Choose what to share in the browser dialog…',
      },
    ]
    return (
      <div className="page-narrow">
        <div className="page-head">
          <div>
            <h1>Record live</h1>
            <p className="lede">Start recording, then get on with your session. Stop when it ends and the notes follow.</p>
          </div>
        </div>
        <div className="setup">
          <div className="field">
            <label htmlFor="live-title">Title</label>
            <input
              id="live-title"
              className="input"
              value={title}
              placeholder="Live session"
              disabled={requesting}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="choices choices-inline" aria-busy={requesting}>
            {choices.map((c) => (
              <button
                key={c.mode}
                type="button"
                className="choice"
                disabled={requesting}
                data-waiting={requesting && mode === c.mode ? '' : undefined}
                onClick={() => void start(c.mode)}
              >
                <Icon name={c.icon} />
                <strong>{c.label}</strong>
                <span>{requesting && mode === c.mode ? c.waiting : c.detail}</span>
              </button>
            ))}
          </div>
          {error && (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>{error}</p>
              </div>
            </div>
          )}
          {discarded && !error && (
            <div className="banner banner-info" role="status">
              <Icon name="trash" />
              <div className="banner-body">
                <p>The recording was discarded. Nothing was kept.</p>
              </div>
            </div>
          )}
          <p className="setup-note">
            Everything is saved on this computer as it's captured, so closing {desktopApp ? 'Iris' : 'the tab'} keeps
            what was recorded.
          </p>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="recbar" role="region" aria-label="Recording controls">
        <span className={stopped ? 'rec-label rec-label-stopped' : 'rec-label'}>
          <span className="rec-dot" aria-hidden="true" />
          {phase === 'stopping' ? 'Finishing' : stopped ? 'Stopped' : 'Recording'}
          <span className="num" aria-hidden="true">{formatClock(elapsed)}</span>
        </span>
        <span className="capture-status">
          {display && !stopped && (
            <span>
              <Icon name="screen" /> Screen shared
            </span>
          )}
          <span>
            <Icon name="mic" /> Microphone {stopped ? 'off' : 'on'}
          </span>
          {mode === 'audio' &&
            desktopApp &&
            !stopped &&
            (macSound ? (
              <span>
                <Icon name="system" /> Mac's sound on
              </span>
            ) : (
              <span className="warn">
                <Icon name="system" /> Mac's sound off
              </span>
            ))}
        </span>
        <span className="spacer" />
        {recording && (
          <button type="button" className="btn btn-stop" onClick={pause}>
            <Icon name="stop" /> Stop
          </button>
        )}
        {phase === 'stopping' && (
          <button type="button" className="btn btn-stop" disabled>
            Finishing the upload…
          </button>
        )}
      </div>

      <div className="recorder">
        <div
          className={stopped ? 'clock clock-stopped num' : 'clock num'}
          role="timer"
          aria-label={`${stopped ? 'Stopped at' : 'Recording for'} ${formatClock(elapsed)}`}
        >
          {formatClock(elapsed)}
        </div>
        {stopped && (
          <section className="stop-choices" aria-labelledby="stop-choices-title">
            <h2 id="stop-choices-title">Recording stopped at {formatClock(elapsed)}</h2>
            <p>Nothing more is recorded. What would you like to do with it?</p>
            <button type="button" className="btn btn-primary btn-lg" onClick={() => void stopAndOpen()} disabled={busy}>
              <Icon name="check" /> Make notes
            </button>
            <button type="button" className="btn btn-lg" onClick={() => void continueRecording()} disabled={busy}>
              <Icon name="record" /> {continuing ? (mode === 'screen' ? 'Choose what to record…' : 'Continuing…') : 'Continue recording'}
            </button>
            <div className="stop-choices-pair">
              <button type="button" className="btn" onClick={() => setConfirming('start-over')} disabled={busy}>
                <Icon name="retry" /> Start over
              </button>
              <button type="button" className="btn btn-danger-quiet" onClick={() => setConfirming('discard')} disabled={busy}>
                <Icon name="trash" /> {discarding ? 'Discarding…' : 'Discard'}
              </button>
            </div>
          </section>
        )}
        {error && (
          <div className="banner banner-error" role="alert">
            <Icon name="alert" />
            <div className="banner-body">
              <p>{error}</p>
            </div>
          </div>
        )}
        {soundRefused && recording && (
          <div className="banner banner-warning" role="alert">
            <Icon name="alert" />
            <div className="banner-body">
              <p>
                <strong>The Mac's sound isn't being recorded,</strong> only your microphone, so the other
                side of a call is missing. To record it, allow Iris in System Settings → Privacy &amp;
                Security → Screen &amp; System Audio Recording → System Audio Recording Only, then quit and
                reopen Iris.
              </p>
            </div>
          </div>
        )}
        {mode === 'audio' && !desktopApp && !stopped && (
          <p className="after">In a browser, Iris records your microphone only.</p>
        )}
        {display && !stopped && (
          <video
            // The recording view mounts after capture starts, so attach the stream on mount.
            ref={(el) => {
              if (el && el.srcObject !== display) el.srcObject = display
            }}
            className="preview"
            autoPlay
            muted
            playsInline
            aria-label="Preview of the shared screen"
          />
        )}
        <p className="live-progress num">{progress || 'Iris processes the session as you record.'}</p>
        {!stopped && (
          <p className="after">
            When you stop, you choose: make notes, continue, start over or discard.
            {display && ' Stopping the share from the browser makes the notes.'}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={confirming !== null}
        title={confirming === 'start-over' ? 'Start over?' : 'Discard this recording?'}
        body={
          confirming === 'start-over'
            ? `The ${formatClock(elapsed)} recorded so far is deleted, and a new recording starts with the same title.`
            : `The ${formatClock(elapsed)} recorded so far is deleted from this computer. No notes are made. This can't be undone.`
        }
        confirm={confirming === 'start-over' ? 'Delete and start over' : 'Discard'}
        cancel="Keep it"
        danger
        onConfirm={() => void discard(confirming === 'start-over')}
        onCancel={() => setConfirming(null)}
      />

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="Stop recording and leave?"
        body="Leaving this page ends the recording. Iris will make notes from what was captured so far."
        confirm="Stop and leave"
        cancel={stopped ? 'Stay' : 'Keep recording'}
        danger
        busy={phase === 'stopping'}
        onConfirm={() => {
          void stop().then((ok) => (ok ? blocker.proceed?.() : blocker.reset?.()))
        }}
        onCancel={() => blocker.reset?.()}
      />
    </>
  )
}
