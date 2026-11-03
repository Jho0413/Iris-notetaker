import { useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { LiveCapture } from '../capture/liveCapture'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { HealthBanner } from '../components/HealthBanner'
import { Icon } from '../components/Icon'
import { TranscriptList } from '../components/TranscriptList'
import { act } from '../session/request'
import { formatClock, transcriptSegments } from '../session/state'
import { useSessionStream } from '../session/useSessionStream'
import { useTitle } from '../session/useTitle'

/**
 * `unfinished`: capture has stopped, but telling the backend to make notes failed;
 * `finishing`: retrying that request.
 */
type Phase = 'idle' | 'requesting' | 'recording' | 'stopping' | 'unfinished' | 'finishing'

/** Plain words for a capture failure. Messages from the capture module are shown as they are. */
function captureError(err: unknown): string {
  if (!(err instanceof DOMException)) {
    return err instanceof Error ? err.message : `Recording couldn't start: ${String(err)}`
  }
  const name = err.name
  if (name === 'NotAllowedError') {
    return 'Screen sharing or the microphone was blocked or cancelled. Allow both for this site in your browser, then try again.'
  }
  if (name === 'NotFoundError') return 'No microphone was found. Connect one and try again.'
  if (name === 'NotReadableError') return 'The microphone or screen is in use by another app. Close it and try again.'
  return `Recording couldn't start: ${err.message}`
}

export function LivePage() {
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [startedAt, setStartedAt] = useState(0)
  const [now, setNow] = useState(0)
  const capture = useRef<LiveCapture | null>(null)
  const stopping = useRef(false)
  const [display, setDisplay] = useState<MediaStream | null>(null)
  const recording = phase === 'recording'
  const elapsed = recording || phase === 'stopping' ? (now - startedAt) / 1000 : 0

  useTitle(recording ? `● Recording ${formatClock(elapsed)}` : 'Record live')

  const stream = useSessionStream(recording ? sessionId ?? undefined : undefined)
  const segments = useMemo(() => transcriptSegments(stream.run, null), [stream.run])

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

  // If the page goes away mid-recording anyway, end the session so it gets processed.
  useEffect(() => () => void capture.current?.stop().catch(() => {}), [])

  async function start() {
    setError(null)
    setPhase('requesting')
    const { data, error: apiError } = await api
      .POST('/api/live', { body: { title: title.trim() || 'Live session' } })
      .catch(() => ({ data: undefined, error: true }))
    if (apiError || !data) {
      setPhase('idle')
      setError("Can't reach the Iris backend. Start it with make dev, then try again.")
      return
    }
    const live = new LiveCapture(data.id)
    try {
      const screen = await live.start()
      capture.current = live
      // Sharing stopped from the browser's own controls: finish, as the capture module does.
      screen.getVideoTracks()[0]?.addEventListener('ended', () => void stopRef.current())
      setDisplay(screen)
      setSessionId(data.id)
      const t = Date.now()
      setStartedAt(t)
      setNow(t)
      setPhase('recording')
    } catch (err) {
      // Nothing was captured: remove the empty session so it doesn't clutter the library.
      void api.DELETE('/api/sessions/{session_id}', { params: { path: { session_id: data.id } } })
      setPhase('idle')
      setError(captureError(err))
    }
  }

  /**
   * Stop capture and hand over to processing. Stopping tears capture down before it tells the
   * backend to finish, so a failure leaves the recording stopped but unfinished: the page then
   * offers to retry only the finish request (`finish`). What was captured is already saved.
   */
  async function stop(): Promise<boolean> {
    const live = capture.current
    if (!live || stopping.current) return false
    stopping.current = true
    setError(null)
    setPhase('stopping')
    try {
      await live.stop()
      return true
    } catch (err) {
      setPhase('unfinished')
      setError(err instanceof Error && err.message ? err.message : String(err))
      return false
    } finally {
      capture.current = null
      stopping.current = false
    }
  }

  async function finish() {
    if (!sessionId) return
    setPhase('finishing')
    setError(null)
    const result = await act(api.POST('/api/live/{session_id}/end', { params: { path: { session_id: sessionId } } }))
    // 409: the session is no longer recording, so an earlier finish request did arrive.
    if (result.ok || result.status === 409) return navigate(`/sessions/${sessionId}`)
    setPhase('unfinished')
    setError(result.status === 0 ? "Couldn't reach the backend. Is it running?" : `The backend refused (HTTP ${result.status}).`)
  }

  async function stopAndOpen() {
    if (await stop()) navigate(`/sessions/${sessionId}`)
  }
  const stopRef = useRef(stopAndOpen)
  useEffect(() => {
    stopRef.current = stopAndOpen
  })

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
    return (
      <div className="page-narrow">
        <div className="page-head">
          <div>
            <h1>Record live</h1>
            <p className="lede">Start recording, then get on with your session. Stop when it ends and the notes follow.</p>
          </div>
        </div>
        <HealthBanner />
        <form
          className="setup"
          onSubmit={(e) => {
            e.preventDefault()
            void start()
          }}
        >
          <div className="field">
            <label htmlFor="live-title">Title</label>
            <input
              id="live-title"
              className="input"
              value={title}
              placeholder="Live session"
              disabled={phase === 'requesting'}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div>
            <p style={{ margin: '0 0 6px', fontWeight: 500 }}>What Iris records</p>
            <ul>
              <li>One screen, window or browser tab that you choose. Share a tab to include its audio.</li>
              <li>Your microphone.</li>
              <li>Everything is saved on this computer as it's captured, so closing the tab keeps what was recorded.</li>
            </ul>
          </div>
          {error && (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>{error}</p>
              </div>
            </div>
          )}
          <div className="form-actions">
            <button className="btn btn-primary btn-lg" disabled={phase === 'requesting'}>
              <Icon name="record" />
              {phase === 'requesting' ? 'Choose what to share in the browser dialog…' : error ? 'Try again' : 'Start recording'}
            </button>
          </div>
        </form>
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
          <span>
            <Icon name="screen" /> Screen shared
          </span>
          <span>
            <Icon name="mic" /> Microphone on
          </span>
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
        <p className="after">
          When you stop (or stop sharing from the browser), Iris finishes uploading and starts
          writing the notes. You can close this window afterwards and find the session in the
          library.
        </p>
        <details className="disclosure">
          <summary>
            <Icon name="chevron" /> Show live transcript
          </summary>
          <div className="disclosure-body">
            {segments.length ? (
              <TranscriptList segments={segments} follow />
            ) : (
              <p className="placeholder">Nothing transcribed yet. Lines appear here as they're processed.</p>
            )}
          </div>
        </details>
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
