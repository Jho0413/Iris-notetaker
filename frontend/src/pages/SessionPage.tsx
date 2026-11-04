import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, sessionFileUrl, type Notes, type Session, type Transcript } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { FiguresGrid } from '../components/FiguresGrid'
import { Icon } from '../components/Icon'
import { NotesView, type OutlineItem } from '../components/NotesView'
import { ProgressRail } from '../components/ProgressRail'
import { SessionRibbon } from '../components/SessionRibbon'
import { StatusChip } from '../components/StatusChip'
import { StepList } from '../components/StepList'
import { TabPanel, Tabs } from '../components/Tabs'
import { TranscriptList } from '../components/TranscriptList'
import { stepCosts, usageCost, usageRefreshKey } from '../session/cost'
import { againLink, UNREACHABLE } from '../session/messages'
import { act } from '../session/request'
import { formatWhen, SOURCE_LABEL } from '../session/format'
import {
  announcement, currentSteps, failureView, figureList, formatClock, progressView, provenance,
  runSeconds, sessionLength, STAGE_LABELS, stepLine, transcriptSegments, uiState, type FigureState,
  type RunState,
} from '../session/state'
import type { SessionStream } from '../session/useSessionStream'
import { useSessionStream } from '../session/useSessionStream'
import { useSessionUsage } from '../session/useSessionUsage'
import { useTitle } from '../session/useTitle'

type TabId = 'notes' | 'transcript' | 'figures'
type LoadError = 'not-found' | 'unreachable' | null

const capitalize = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

async function fetchSession(id: string): Promise<Session | 'not-found' | 'unreachable'> {
  try {
    const { data, response } = await api.GET('/api/sessions/{session_id}', {
      params: { path: { session_id: id } },
    })
    return data ?? (response.status === 404 ? 'not-found' : 'unreachable')
  } catch {
    return 'unreachable'
  }
}

async function fetchNotes(id: string): Promise<Notes | undefined> {
  const { data } = await api
    .GET('/api/sessions/{session_id}/notes', { params: { path: { session_id: id } } })
    .catch(() => ({ data: undefined }))
  return data
}

async function fetchTranscript(id: string): Promise<Transcript | undefined> {
  const { data } = await api
    .GET('/api/sessions/{session_id}/transcript', { params: { path: { session_id: id } } })
    .catch(() => ({ data: undefined }))
  return data
}

/** Route entry: a fresh page per session id, so no state leaks between sessions. */
export function SessionPage() {
  const { id } = useParams<{ id: string }>()
  return id ? <SessionView key={id} id={id} /> : null
}

function SessionView({ id }: { id: string }) {
  const navigate = useNavigate()
  const stream = useSessionStream(id)
  const run = stream.run

  const [session, setSession] = useState<Session | null>(null)
  const [loadError, setLoadError] = useState<LoadError>(null)
  const [notes, setNotes] = useState<Notes | null>(null)
  const [notesLoaded, setNotesLoaded] = useState(false)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [tab, setTab] = useState<TabId>('notes')
  const [outline, setOutline] = useState<OutlineItem[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const [reload, setReload] = useState(0)

  // The stored session follows status changes and errors.
  useEffect(() => {
    let live = true
    void fetchSession(id).then((result) => {
      if (!live) return
      if (typeof result === 'string') setLoadError(result)
      else {
        setSession(result)
        setLoadError(null)
      }
    })
    return () => {
      live = false
    }
  }, [id, run.status, run.failure, reload])

  // Only the finished notes are shown, so notes and transcript are fetched once the session is
  // done. Leaving done (a re-run) drops them: the old run's notes are never shown meanwhile.
  const done = (run.status ?? session?.status) === 'done'
  const [wasDone, setWasDone] = useState(done)
  if (done !== wasDone) {
    // Reset while rendering (not in an effect), so stale notes never reach the screen.
    setWasDone(done)
    if (!done) {
      setNotes(null)
      setNotesLoaded(false)
      setTranscript(null)
    }
  }
  useEffect(() => {
    if (!done) return
    let live = true
    void fetchNotes(id).then((data) => {
      if (!live) return
      setNotes(data ?? null)
      setNotesLoaded(true)
    })
    void fetchTranscript(id).then((data) => live && data && setTranscript(data))
    return () => {
      live = false
    }
  }, [id, done, reload])

  // Cost: fetched when a step or the session finishes, not on every event.
  const status = run.status ?? session?.status
  const usage = useSessionUsage(id, usageRefreshKey(run, status))
  const cost = usage && status ? usageCost(usage, status) : null
  const costs = useMemo(() => (usage ? stepCosts(usage) : []), [usage])

  const ui = session
    ? uiState(session, run, { stalled: stream.quiet && (run.status ?? session.status) === 'processing' })
    : null
  useTitle(session?.title)

  const segments = useMemo(() => transcriptSegments(run, transcript), [run, transcript])
  const figures = useMemo(() => figureList(run, notes), [run, notes])
  const figureMeta = useMemo(
    () => new Map(figures.filter((f) => f.path).map((f) => [f.path!, { t: f.t, provenance: provenance(f) }])),
    [figures],
  )
  const transcriptKnown = transcript !== null || run.stages.transcribe.status === 'done'
  const length = session && transcriptKnown ? sessionLength(session, segments) : undefined

  async function rerun() {
    if (!ui) return
    setBusy(true)
    setActionError(null)
    const result = await act(
      api.POST('/api/sessions/{session_id}/run', {
        params: { path: { session_id: id }, query: ui.fromStage ? { from_stage: ui.fromStage } : {} },
      }),
    )
    setBusy(false)
    if (!result.ok) {
      setActionError(result.status === 409 ? 'This session is already processing.' : UNREACHABLE)
    }
    setReload((n) => n + 1)
  }

  async function finishLive() {
    setBusy(true)
    setActionError(null)
    const result = await act(api.POST('/api/live/{session_id}/end', { params: { path: { session_id: id } } }))
    setBusy(false)
    if (!result.ok) {
      setActionError(result.status === 0 ? UNREACHABLE : "Couldn't finish the recording. It may already be processing.")
    }
    setReload((n) => n + 1)
  }

  async function deleteSession() {
    setBusy(true)
    const result = await act(api.DELETE('/api/sessions/{session_id}', { params: { path: { session_id: id } } }))
    setBusy(false)
    setConfirmDelete(false)
    if (result.ok) navigate('/')
    else setActionError(result.status === 0 ? UNREACHABLE : "Couldn't delete this session. It may be processing.")
  }

  function showFigure(f: FigureState) {
    const img = f.path && notes
      ? document.querySelector<HTMLImageElement>(`.notes img[src="${CSS.escape(sessionFileUrl(notes.session_id, f.path))}"]`)
      : null
    if (img) {
      setTab('notes')
      const figure = img.closest('figure') ?? img
      requestAnimationFrame(() => {
        figure.scrollIntoView({ behavior: 'smooth', block: 'center' })
        figure.classList.add('flash')
        window.setTimeout(() => figure.classList.remove('flash'), 1600)
      })
    } else {
      setTab('figures')
      requestAnimationFrame(() => document.getElementById(`figure-${f.id}`)?.scrollIntoView({ block: 'center' }))
    }
  }

  if (loadError === 'not-found') {
    return (
      <div className="page error-panel">
        <h2>This session doesn't exist</h2>
        <p>It may have been deleted.</p>
        <Link className="btn" to="/">
          Back to the library
        </Link>
      </div>
    )
  }
  if (!session || !ui) {
    if (loadError === 'unreachable') {
      return (
        <div className="page error-panel" role="alert">
          <h2>{UNREACHABLE}</h2>
          <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
            <Icon name="retry" /> Retry
          </button>
        </div>
      )
    }
    return (
      <div className="session" aria-busy="true" aria-label="Loading session">
        <div className="skeleton sk-line" style={{ width: 120 }} />
        <div className="skeleton" style={{ height: 28, width: '40%', margin: '12px 0' }} />
        <div className="skeleton sk-line" style={{ width: '25%' }} />
        <div className="session-body">
          <div>
            {[90, 100, 95, 60, 100, 85].map((w, i) => (
              <div key={i} className="skeleton sk-line" style={{ width: `${w}%` }} />
            ))}
          </div>
          <div className="skeleton" style={{ height: 260 }} />
        </div>
      </div>
    )
  }

  const finished = ui.kind === 'finished'
  const progress = progressView(ui.kind)
  const working = ui.kind === 'processing' || ui.kind === 'starting' || ui.kind === 'polishing'
  const sessionType = notes?.session_type
  const fromLabel = ui.fromStage ? STAGE_LABELS[ui.fromStage] : 'the start'
  const failure = ui.kind === 'failed' ? failureView(session, run) : null
  const again = againLink(session.source)

  return (
    <div className="session">
      <div className="sr-only" aria-live="polite">
        {announcement(ui)}
      </div>
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Library</Link>
        <Icon name="chevron" />
        <span aria-current="page">{session.title}</span>
      </nav>
      <header className="session-head">
        <div className="titles">
          <h1>{session.title || 'Untitled session'}</h1>
          <div className="session-meta">
            {sessionType && <span>{capitalize(sessionType)}</span>}
            <span>{SOURCE_LABEL[session.source] ?? session.source}</span>
            {length !== undefined && <span className="num">{formatClock(length)}</span>}
            <span className="num">{formatWhen(session.created_at)}</span>
          </div>
        </div>
        <div className="head-actions">
          <StatusChip state={ui} />
          {notes && (
            <a className={finished ? 'btn btn-primary' : 'btn'} href={`/api/sessions/${session.id}/export`} download>
              <Icon name="download" /> Export Markdown
            </a>
          )}
          {(failure?.kind === 'general' || ui.kind === 'stalled') && (
            <button type="button" className="btn btn-primary" onClick={() => void rerun()} disabled={busy}>
              <Icon name="retry" /> {ui.kind === 'failed' ? 'Try again' : 'Resume'}
            </button>
          )}
          {failure?.kind === 'input' && (
            <Link className="btn btn-primary" to={again.to}>
              {again.label}
            </Link>
          )}
          {ui.kind === 'recording-orphan' && (
            <button type="button" className="btn btn-primary" onClick={() => void finishLive()} disabled={busy}>
              Finish and make notes
            </button>
          )}
          {!working && (
            <button
              type="button"
              className="btn btn-ghost"
              aria-label="Delete session"
              title="Delete session"
              onClick={() => setConfirmDelete(true)}
            >
              <Icon name="trash" />
            </button>
          )}
        </div>
      </header>

      {finished && length !== undefined && figures.some((f) => f.t !== undefined) && (
        <SessionRibbon length={length} figures={figures} onSelect={showFigure} />
      )}

      <div className="session-body">
        <div>
          {actionError && (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>{actionError}</p>
              </div>
            </div>
          )}
          {failure && (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>
                  <strong>{failure.headline}.</strong> {failure.message}
                </p>
                {failure.kind === 'general' && <p>Try again resumes from {fromLabel}.</p>}
              </div>
            </div>
          )}
          {ui.kind === 'stalled' && (
            <div className="banner banner-warning" role="status">
              <Icon name="alert" />
              <div className="banner-body">
                <p>
                  <strong>Processing stopped unexpectedly.</strong> Iris probably restarted while this
                  session was running. Resume picks up from {fromLabel} and reuses finished steps.
                </p>
              </div>
            </div>
          )}
          {ui.kind === 'recording-orphan' && (
            <div className="banner banner-info" role="status">
              <Icon name="record" />
              <div className="banner-body">
                <p>
                  <strong>This live session was never stopped.</strong> If it's still recording in
                  another tab, stop it there. Otherwise, finish it here to make notes from what was
                  captured.
                </p>
              </div>
            </div>
          )}

          {progress ? (
            <ProgressView
              view={progress}
              session={session}
              run={run}
              stream={stream}
            />
          ) : (
            <>
              <Tabs<TabId>
                label="Session content"
                idPrefix="session"
                active={tab}
                onChange={setTab}
                tabs={[
                  { id: 'notes', label: 'Notes' },
                  { id: 'transcript', label: 'Transcript', count: segments.length ? String(segments.length) : undefined },
                  { id: 'figures', label: 'Figures', count: figures.length ? String(figures.length) : undefined },
                ]}
              />

              <TabPanel id="notes" idPrefix="session" active={tab === 'notes'}>
                {notes ? (
                  <NotesView
                    sessionId={session.id}
                    title={notes.title}
                    markdown={notes.markdown}
                    figures={figureMeta}
                    hideTitle={notes.title.trim() === session.title.trim()}
                    onOutline={setOutline}
                  />
                ) : notesLoaded ? (
                  <div className="placeholder">
                    <h2>The notes couldn't be loaded</h2>
                    <p>Reload the page to try again.</p>
                  </div>
                ) : (
                  <div aria-busy="true" aria-label="Loading notes">
                    {[90, 100, 95, 60, 100, 85].map((w, i) => (
                      <div key={i} className="skeleton sk-line" style={{ width: `${w}%` }} />
                    ))}
                  </div>
                )}
              </TabPanel>

              <TabPanel id="transcript" idPrefix="session" active={tab === 'transcript'}>
                {segments.length ? (
                  <TranscriptList segments={segments} />
                ) : (
                  <div className="placeholder">
                    <h2>No speech was transcribed</h2>
                  </div>
                )}
              </TabPanel>

              <TabPanel id="figures" idPrefix="session" active={tab === 'figures'}>
                {figures.length ? (
                  <FiguresGrid sessionId={session.id} figures={figures} />
                ) : (
                  <div className="placeholder">
                    <h2>No figures in this session</h2>
                    <p>Iris found nothing on screen worth redrawing.</p>
                  </div>
                )}
              </TabPanel>
            </>
          )}
        </div>

        <ProgressRail
          run={run}
          finished={finished}
          hasEvents={stream.eventCount > 0}
          currentStage={session.current_stage}
          outline={notes ? outline : []}
          stream={stream.status}
          showProgress={finished && stream.eventCount > 0}
          cost={cost}
          stepCosts={costs}
        />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title={`Delete "${session.title}"?`}
        body="This removes the recording, transcript, figures and notes from this computer. It can't be undone."
        confirm="Delete session"
        danger
        busy={busy}
        onConfirm={() => void deleteSession()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  )
}

/**
 * Until the notes are finished, the main column shows where processing is instead of any notes:
 * the state, the current step, every step, and how long the run has taken. The step list keeps one row per step, so nothing moves as steps advance.
 */
function ProgressView({ view, session, run, stream }: {
  view: { heading: string; active: boolean }
  session: Session
  run: RunState
  stream: SessionStream
}) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!view.active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [view.active])
  const steps = currentSteps(session, run)
  const seconds = view.active ? runSeconds(run, now) : undefined
  return (
    <section className="progress-view" aria-labelledby="progress-heading">
      <h2 id="progress-heading">{view.heading}</h2>
      {view.active && (
        <>
          <p className="progress-current">
            {steps.length ? steps.map((s) => stepLine(s, run)).join(' · ') : 'Getting started…'}
          </p>
          <p className="progress-elapsed">
            {seconds !== undefined ? (
              <>
                Running for <span className="num">{formatClock(seconds)}</span>
              </>
            ) : (
              '\u00a0'
            )}
          </p>
        </>
      )}
      <StepList
        className="progress-steps"
        run={run}
        finished={false}
        hasEvents={stream.eventCount > 0}
        currentStage={session.current_stage}
      />
      {view.active && (
        <p className="progress-note">
          Your notes appear here when they're finished. You can leave this page: processing carries
          on, and the session is in the library.
        </p>
      )}
    </section>
  )
}
