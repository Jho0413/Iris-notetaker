import { useEffect, useRef, useState } from 'react'
import { useBlocker, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { CaptureError, inDesktopApp } from '../capture/captureErrors'
import { EndSessionError } from '../capture/endSession'
import { type CaptureMode, LiveCapture } from '../capture/liveCapture'
import { menuBar, type LiveLocationState } from '../capture/menuBar'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Icon } from '../components/Icon'
import { UNREACHABLE } from '../session/messages'
import { act } from '../session/request'
import { formatClock, liveProgress } from '../session/state'
import { useSessionStream } from '../session/useSessionStream'
import { useTitle } from '../session/useTitle'
import { irisApp } from '../settings/appBridge'

/**
 * `unfinished`: capture has stopped, but telling the backend to make notes failed;
 * `finishing`: retrying that request.
 */
type Phase = 'idle' | 'requesting' | 'recording' | 'stopping' | 'unfinished' | 'finishing'

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
  const [startedAt, setStartedAt] = useState(0)
  const [now, setNow] = useState(0)
  const capture = useRef<LiveCapture | null>(null)
  const stopping = useRef(false)
  const unmounted = useRef(false)
  const [mode, setMode] = useState<CaptureMode>('screen')
  const [display, setDisplay] = useState<MediaStream | null>(null)
  const [macSound, setMacSound] = useState(false)
  const desktopApp = inDesktopApp()
  // macOS refused the Mac's sound: only the mic is recorded (a browser never has it).
  const macSoundRefused = desktopApp && mode === 'audio' && !macSound && phase === 'recording'
  const recording = phase === 'recording'
  const elapsed = recording || phase === 'stopping' ? (now - startedAt) / 1000 : 0

  useTitle(recording ? `● Recording ${formatClock(elapsed)}` : 'Record live')

  // What live processing has done so far, as one quiet line (not announced: it changes often).
  const stream = useSessionStream(recording || phase === 'stopping' ? sessionId ?? undefined : undefined)
  const progress = liveProgress(stream.run)

  // Leaving ends the recording: the browser asks before closing the tab, the app before navigating.
  const blocker = useBlocker(recording)
  useEffect(() => {
    if (!recording) return
    const onBeforeUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onBeforeUnload)
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => {
      window.removeEventListener('beforeunload', onBeforeUnload)
      window.clearInterval(timer)
    }
  }, [recording])

  // If the page goes away mid-recording anyway, end the session so it gets processed. During a
  // start, stopping cancels it: everything granted is released and the empty session discarded.
  // Until that stop settles the app's menu bar says the upload is finishing, so quitting still asks
  // (capture/menuBar.ts: chunks not yet uploaded are only in this window's memory).
  useEffect(() => {
    unmounted.current = false
    return () => {
      unmounted.current = true
      menuBar.left(capture.current?.stop())
    }
  }, [])

  async function start(mode: CaptureMode) {
    setError(null)
    setMode(mode)
    setPhase('requesting')
    const { data, error: apiError } = await api
      .POST('/api/live', { body: { title: title.trim() || 'Live session', screen: mode === 'screen' } })
      .catch(() => ({ data: undefined, error: true }))
    if (apiError || !data) {
      setPhase('idle')
      setError(UNREACHABLE)
      return
    }
    if (unmounted.current) {
      // Left the page while the session was being created: discard it, don't ask for the screen.
      void api
        .DELETE('/api/sessions/{session_id}', { params: { path: { session_id: data.id } } })
        .catch(() => {})
      return
    }
    // Sharing stopped from the browser's own controls, the mic went away or the recorder failed:
    // finish. Held before start() so leaving the page during a prompt cancels the start.
    const live = new LiveCapture(data.id, {
      onEnded: () => void stopRef.current(),
      onMacSoundEnded: () => setMacSound(false),
    })
    capture.current = live
    try {
      const started = await live.start(mode)
      setDisplay(started.screen)
      setMacSound(started.macSound)
      setSessionId(data.id)
      const t = Date.now()
      setStartedAt(t)
      setNow(t)
      setPhase('recording')
    } catch (err) {
      // Nothing was captured, and the capture module has already discarded the empty session.
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
      return live.sessionId
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

  async function stopAndOpen() {
    // The id comes from the capture: `sessionId` state can lag if capture ends right after starting.
    const id = await stop()
    if (id) navigate(`/sessions/${id}`)
  }
  const stopRef = useRef(stopAndOpen)
  /** Start, unless a recording is already under way (or stopped and not yet finished). */
  function startIfIdle(mode: CaptureMode) {
    if (phase === 'idle') void start(mode)
  }
  const startRef = useRef(startIfIdle)
  useEffect(() => {
    stopRef.current = stopAndOpen
    startRef.current = startIfIdle
  })

  // The desktop app's menu bar icon (capture/menuBar.ts). It shows what this page is doing, and
  // its Record and Stop go through the same start() and stop as the buttons here.
  useEffect(() => {
    menuBar.phase(phase, startedAt, macSoundRefused)
  }, [phase, startedAt, macSoundRefused])
  useEffect(
    () =>
      irisApp()?.onRecordCommand((command) => {
        if (command === 'stop') void stopRef.current()
      }),
    [],
  )
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
        <span className="rec-label">
          <span className="rec-dot" aria-hidden="true" />
          {phase === 'stopping' ? 'Finishing' : 'Recording'}
          <span className="num" aria-hidden="true">{formatClock(elapsed)}</span>
        </span>
        <span className="capture-status">
          {display && (
            <span>
              <Icon name="screen" /> Screen shared
            </span>
          )}
          <span>
            <Icon name="mic" /> Microphone on
          </span>
          {mode === 'audio' &&
            (macSound ? (
              <span>
                <Icon name="system" /> Mac's sound on
              </span>
            ) : (
              <span className="warn">
                <Icon name="system" /> {desktopApp ? "Mac's sound off" : 'Microphone only'}
              </span>
            ))}
        </span>
        <span className="spacer" />
        <button type="button" className="btn btn-stop" onClick={() => void stopAndOpen()} disabled={phase === 'stopping'}>
          <Icon name="stop" />
          {phase === 'stopping' ? 'Finishing the upload…' : 'Stop and make notes'}
        </button>
      </div>

      <div className="recorder">
        <div className="clock num" role="timer" aria-label={`Recording for ${formatClock(elapsed)}`}>
          {formatClock(elapsed)}
        </div>
        {error && (
          <div className="banner banner-error" role="alert">
            <Icon name="alert" />
            <div className="banner-body">
              <p>{error}</p>
            </div>
          </div>
        )}
        {macSoundRefused && (
          <div className="banner banner-warning" role="alert">
            <Icon name="alert" />
            <div className="banner-body">
              <p>
                <strong>The Mac's sound isn't being recorded,</strong> only your microphone, so the other
                side of a call is missing. To record it, allow Iris in System Settings → Privacy &amp;
                Security → Screen &amp; System Audio Recording → System Audio Recording Only, then start a
                new recording.
              </p>
            </div>
          </div>
        )}
        {mode === 'audio' && !desktopApp && (
          <p className="after">In a browser, Iris records your microphone only.</p>
        )}
        {display && (
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
        <p className="after">
          {display
            ? 'When you stop (or stop sharing from the browser), Iris uploads the rest and finishes the notes.'
            : 'When you stop, Iris uploads the rest and finishes the notes.'}{' '}
          You can close this window afterwards and find the session in the library.
        </p>
      </div>

      <ConfirmDialog
        open={blocker.state === 'blocked'}
        title="Stop recording and leave?"
        body="Leaving this page ends the recording. Iris will make notes from what was captured so far."
        confirm="Stop and leave"
        cancel="Keep recording"
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
