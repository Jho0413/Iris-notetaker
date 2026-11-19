import { useEffect, useRef, useState } from 'react'
import { type Location, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { act } from '../api/request'
import { CaptureError } from '../capture/captureErrors'
import { EndSessionError } from '../capture/endSession'
import {
  type CaptureMode,
  canShareScreen,
  clockStart,
  LiveCapture,
  macSoundRefused,
  pausedClock,
  type RecordingClock,
  recordedMs,
} from '../capture/liveCapture'
import { menuBar, type LiveLocationState } from '../capture/menuBar'
import { Banner } from '../components/Banner'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { Icon, type IconName } from '../components/Icon'
import { UNREACHABLE } from '../lib/messages'
import { formatClock } from '../lib/format'
import { liveProgress } from '../session/state'
import { useSessionStream } from '../session/useSessionStream'
import { useAskBeforeLeaving } from '../hooks/useAskBeforeLeaving'
import { useTitle } from '../hooks/useTitle'
import { irisApp, type Phase, type RecordCommand } from '../api/appBridge'

type Destructive = 'discard' | 'start-over'

function captureError(err: unknown): string {
  return err instanceof CaptureError ? err.message : "Recording couldn't start. Try again."
}

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
  const [clock, setClock] = useState<RecordingClock>({ recordedMs: 0, since: 0 })
  const [now, setNow] = useState(0)
  const [discarding, setDiscarding] = useState(false)
  const [confirming, setConfirming] = useState<Destructive | null>(null)
  const [discarded, setDiscarded] = useState(false)
  const capture = useRef<LiveCapture | null>(null)
  const stopping = useRef(false)
  const [leavingTo, setLeavingTo] = useState<Location | null>(null)
  const [mode, setMode] = useState<CaptureMode>('screen')
  const [display, setDisplay] = useState<MediaStream | null>(null)
  const [sharing, setSharing] = useState(false)
  const [macSound, setMacSound] = useState(false)
  const desktopApp = Boolean(irisApp())
  const canShare = typeof navigator !== 'undefined' && canShareScreen(navigator.mediaDevices)
  const recording = phase === 'recording'
  const continuing = phase === 'continuing'
  const stopped = phase === 'stopped' || continuing
  const busy = continuing || discarding
  const open = recording || stopped
  const soundRefused = open && macSoundRefused(desktopApp, { macSound })
  const elapsed = recordedMs(clock, now) / 1000

  useTitle(recording ? `● Recording ${formatClock(elapsed)}` : stopped ? `Stopped ${formatClock(elapsed)}` : 'Record live')

  const stream = useSessionStream(open || phase === 'stopping' ? sessionId ?? undefined : undefined)
  const progress = liveProgress(stream.run)

  const blocker = useAskBeforeLeaving(open)
  useEffect(() => {
    if (!recording) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [recording])

  useEffect(() => () => menuBar.left(capture.current?.keep()), [])

  async function start(mode: CaptureMode) {
    setError(null)
    setDiscarded(false)
    setMode(mode)
    setPhase('requesting')
    const createSession = async () => {
      const { data } = await act(api.POST('/api/live', { body: { title: title.trim() || 'Live session' } }))
      if (!data) throw new CaptureError(UNREACHABLE)
      return data.id
    }
    const live = new LiveCapture(createSession, {
      onEnded: () => void stopRef.current(),
      onScreenEnded: () => setDisplay(null),
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
      capture.current = null
      setPhase('idle')
      setError(captureError(err))
    }
  }

  async function stop(keep = false): Promise<string | null> {
    const live = capture.current
    if (!live || stopping.current) return null
    stopping.current = true
    setError(null)
    setPhase('stopping')
    try {
      await (keep ? live.keep() : live.stop())
      capture.current = null
      return live.sessionId ?? null
    } catch (err) {
      setPhase('unfinished')
      setError(finishError(err))
      return null
    } finally {
      stopping.current = false
    }
  }

  async function finish() {
    const live = capture.current
    if (!live || !sessionId) return
    setPhase('finishing')
    setError(null)
    try {
      await (leavingTo ? live.keep() : live.stop())
      capture.current = null
      navigate(leavingTo ?? `/sessions/${sessionId}`)
    } catch (err) {
      setPhase('unfinished')
      setError(finishError(err))
    }
  }

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

  async function continueRecording() {
    const live = capture.current
    if (!live || phase !== 'stopped' || busy) return
    setPhase('continuing')
    setError(null)
    try {
      const again = await live.resume()
      setDisplay(again.screen)
      setMacSound(again.macSound)
      const t = Date.now()
      setClock((c) => ({ ...c, since: t }))
      setNow(t)
      setPhase('recording')
    } catch (err) {
      setPhase('stopped')
      setError(captureError(err))
    }
  }

  async function shareScreen() {
    const live = capture.current
    if (!live || phase !== 'recording' || sharing || display) return
    setSharing(true)
    setError(null)
    try {
      const shared = await live.startScreen()
      if (shared) setDisplay(shared)
    } catch (err) {
      setError(captureError(err))
    } finally {
      setSharing(false)
    }
  }

  function stopSharing() {
    if (phase !== 'recording') return
    capture.current?.stopScreen()
    setDisplay(null)
  }

  async function discard(again: boolean) {
    const live = capture.current
    setConfirming(null)
    if (!live || busy) return
    setDiscarding(true)
    setError(null)
    try {
      await live.discard()
    } catch (err) {
      setError(captureError(err))
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
    const id = await stop()
    if (id) navigate(`/sessions/${id}`)
  }
  const stopRef = useRef(stopAndOpen)
  const commandRef = useRef<(command: RecordCommand) => void>(() => {})
  function startIfIdle(mode: CaptureMode) {
    if (phase === 'idle') void start(mode)
  }
  const startRef = useRef(startIfIdle)
  useEffect(() => {
    stopRef.current = stopAndOpen
    startRef.current = startIfIdle
    commandRef.current = (command) => {
      if (command === 'stop') pause()
      else if (command === 'share-screen') void shareScreen()
      else if (command === 'stop-sharing') stopSharing()
      else if (command === 'continue') void continueRecording()
      else if (command === 'make-notes' && phase === 'stopped' && !busy) void stopAndOpen()
      else if ((command === 'start-over' || command === 'discard') && phase === 'stopped' && !busy) {
        setConfirming(command)
      }
    }
  })

  const reportedStart = recording ? clockStart(clock) : 0
  const screenOn = recording && display !== null
  useEffect(() => {
    menuBar.phase(phase, reportedStart, soundRefused, screenOn)
  }, [phase, reportedStart, soundRefused, screenOn])
  useEffect(() => irisApp()?.onRecordCommand((command) => commandRef.current(command)), [])
  const startRequest = (location.state as LiveLocationState | null)?.startRequest
  const handledStart = useRef<number | undefined>(undefined)
  useEffect(() => {
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
        <Banner
          tone="error"
          details={
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
          }
        >
          <strong>{leavingTo ? "Iris couldn't finish keeping the recording." : "Iris couldn't start making the notes."}</strong>{' '}
          {error}
        </Banner>
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
          ? 'Also a screen or window you choose.'
          : 'Also a screen, window or tab you choose. Share a tab to include its sound.',
        waiting: desktopApp
          ? 'Allow the microphone, then choose what to record…'
          : 'Allow the microphone, then choose what to share…',
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
          {error && <Banner tone="error">{error}</Banner>}
          {discarded && !error && <Banner tone="info" icon="trash">The recording was discarded. Nothing was kept.</Banner>}
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
          {display && !stopped && <CaptureItem icon="screen" words="Screen shared" />}
          <CaptureItem icon="mic" words={`Microphone ${stopped ? 'off' : 'on'}`} />
          {desktopApp &&
            !stopped &&
            (macSound ? (
              <CaptureItem icon="system" words="Mac's sound on" />
            ) : (
              <CaptureItem icon="system" words="Mac's sound off" warn />
            ))}
        </span>
        <span className="spacer" />
        {recording &&
          canShare &&
          (display ? (
            <button type="button" className="btn" onClick={stopSharing} aria-label="Stop sharing">
              <Icon name="screen" /> <span className="btn-label">Stop sharing</span>
            </button>
          ) : (
            <button
              type="button"
              className="btn"
              onClick={() => void shareScreen()}
              disabled={sharing}
              aria-label={sharing ? 'Choose what to share' : 'Share screen'}
            >
              <Icon name="screen" /> <span className="btn-label">{sharing ? 'Choose what to share…' : 'Share screen'}</span>
            </button>
          ))}
        {recording && (
          <button type="button" className="btn btn-stop" onClick={pause} aria-label="Stop">
            <Icon name="stop" /> <span className="btn-label">Stop</span>
          </button>
        )}
        {phase === 'stopping' && (
          <button type="button" className="btn btn-stop" disabled aria-label="Finishing the upload…">
            <Icon name="spinner" /> <span className="btn-label">Finishing the upload…</span>
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
              <Icon name="record" /> {continuing ? (display ? 'Choose what to record…' : 'Continuing…') : 'Continue recording'}
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
        {error && <Banner tone="error">{error}</Banner>}
        {soundRefused && recording && (
          <Banner tone="warning" role="alert">
            <strong>The Mac's sound isn't being recorded,</strong> only your microphone, so the other
            side of a call is missing. To record it, allow Iris in System Settings → Privacy &amp;
            Security → Screen &amp; System Audio Recording → System Audio Recording Only, then quit and
            reopen Iris.
          </Banner>
        )}
        {!desktopApp && !display && !stopped && (
          <p className="after">
            In a browser, only your microphone is recorded while nothing is shared. To include a call's sound,
            share its tab.
          </p>
        )}
        {display && !stopped && (
          <video
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
            {display && ' If sharing stops, the screen turns off and the recording carries on.'}
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
        body="Iris keeps what was recorded: make notes from it in the library."
        confirm="Stop and leave"
        cancel={stopped ? 'Stay' : 'Keep recording'}
        danger
        busy={phase === 'stopping'}
        onConfirm={() => {
          if (blocker.location) setLeavingTo(blocker.location)
          void stop(true).then((ok) => (ok ? blocker.proceed?.() : blocker.reset?.()))
        }}
        onCancel={() => blocker.reset?.()}
      />
    </>
  )
}

function CaptureItem(props: { icon: IconName; words: string; warn?: boolean }) {
  return (
    <span className={props.warn ? 'warn' : undefined} title={props.words}>
      <Icon name={props.icon} /> <span className="capture-words">{props.words}</span>
    </span>
  )
}
