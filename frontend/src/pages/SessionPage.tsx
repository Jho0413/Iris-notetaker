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
import { TabPanel, Tabs } from '../components/Tabs'
import { TranscriptList } from '../components/TranscriptList'
import { draftRefreshKey, fetchDraft, type NotesDraft } from '../session/draft'
import { act } from '../session/request'
import { formatWhen, SOURCE_LABEL } from '../session/format'
import {
  failureHeadline, figureList, formatClock, provenance, sessionLength, STAGE_LABELS,
  transcriptSegments, uiState, type FigureState,
} from '../session/state'
import { useSessionStream } from '../session/useSessionStream'
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
  const [draft, setDraft] = useState<NotesDraft | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [tab, setTab] = useState<TabId>('notes')
  const [outline, setOutline] = useState<OutlineItem[]>([])
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const [reload, setReload] = useState(0)

  // The stored session follows status changes and errors; notes and transcript follow their stages.
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
  }, [id, run.status, run.error, reload])

  useEffect(() => {
    let live = true
    void fetchNotes(id).then((data) => live && data && setNotes(data))
    return () => {
      live = false
    }
  }, [id, run.notes?.final, run.stages.synthesize.status, run.status, reload])

  useEffect(() => {
    let live = true
    void fetchTranscript(id).then((data) => live && data && setTranscript(data))
    return () => {
      live = false
    }
  }, [id, run.stages.transcribe.status, reload])

  // The draft while recording or processing: once on load, then on every `notes_updated`. A failed
  // fetch keeps the draft already shown.
  const draftKey = draftRefreshKey(run.status ?? session?.status, run)
  useEffect(() => {
    if (draftKey === null) return
    let live = true
    void fetchDraft(id).then((d) => {
      if (live && d !== undefined) setDraft(d)
    })
    return () => {
      live = false
    }
  }, [id, draftKey])

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
      setActionError(result.status === 409 ? 'This session is already processing.' : "Couldn't restart processing. Is the backend running?")
    }
    setReload((n) => n + 1)
  }

  async function finishLive() {
    setBusy(true)
    setActionError(null)
    const result = await act(api.POST('/api/live/{session_id}/end', { params: { path: { session_id: id } } }))
    setBusy(false)
    if (!result.ok) {
      setActionError(result.status === 0 ? "Couldn't reach the backend. Is it running?" : "Couldn't finish the recording. It may already be processing.")
    }
    setReload((n) => n + 1)
  }

  async function deleteSession() {
    setBusy(true)
    const result = await act(api.DELETE('/api/sessions/{session_id}', { params: { path: { session_id: id } } }))
    setBusy(false)
    setConfirmDelete(false)
    if (result.ok) navigate('/')
    else setActionError(result.status === 0 ? "Couldn't reach the backend. Is it running?" : "Couldn't delete this session. It may be processing.")
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
          <h2>Can't reach the Iris backend</h2>
          <p>
            Start it with <code>make dev</code>, then try again.
          </p>
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
  const working =
    ui.kind === 'processing' || ui.kind === 'draft' || ui.kind === 'starting' || ui.kind === 'polishing'
  const sessionType = notes?.session_type ?? draft?.session_type
  const rendered = figures.filter((f) => f.rendered).length
  const fromLabel = ui.fromStage ? STAGE_LABELS[ui.fromStage] : 'the start'

  return (
    <div className="session">
      <div className="sr-only" aria-live="polite">
        {ui.label}
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
          {(ui.kind === 'failed' || ui.kind === 'stalled') && (
            <button type="button" className="btn btn-primary" onClick={() => void rerun()} disabled={busy}>
              <Icon name="retry" /> {ui.kind === 'failed' ? 'Try again' : 'Resume'}
            </button>
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

      {length !== undefined && figures.some((f) => f.t !== undefined) && (
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
          {ui.kind === 'failed' && (
            <div className="banner banner-error" role="alert">
              <Icon name="alert" />
              <div className="banner-body">
                <p>
                  <strong>{failureHeadline(session.error ?? run.error)}.</strong> The full message is
                  under Details.
                </p>
                <p>Try again resumes from {fromLabel}. Steps that already finished are reused, so it costs little.</p>
              </div>
            </div>
          )}
          {ui.kind === 'stalled' && (
            <div className="banner banner-warning" role="status">
              <Icon name="alert" />
              <div className="banner-body">
                <p>
                  <strong>Processing stopped unexpectedly.</strong> The backend probably restarted while
                  this session was running. Resume picks up from {fromLabel} and reuses finished steps.
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

          <Tabs<TabId>
            label="Session content"
            idPrefix="session"
            active={tab}
            onChange={setTab}
            tabs={[
              { id: 'notes', label: 'Notes' },
              { id: 'transcript', label: 'Transcript', count: segments.length ? String(segments.length) : undefined },
              {
                id: 'figures',
                label: 'Figures',
                count: figures.length ? (working ? `${rendered}/${figures.length}` : String(figures.length)) : undefined,
              },
            ]}
          />

          <TabPanel id="notes" idPrefix="session" active={tab === 'notes'}>
            {notes ? (
              <>
                {!finished && (
                  <div className="banner banner-info" role="note">
                    <Icon name="file" />
                    <div className="banner-body">
                      <p>
                        {ui.kind === 'polishing'
                          ? "These notes are ready to read. A final pass is tidying them across the whole session; they'll update when it finishes."
                          : "These notes are from an earlier run. They'll be replaced when this one finishes."}
                      </p>
                    </div>
                  </div>
                )}
                <NotesView
                  sessionId={session.id}
                  title={notes.title}
                  markdown={notes.markdown}
                  figures={figureMeta}
                  hideTitle={notes.title.trim() === session.title.trim()}
                  onOutline={setOutline}
                />
              </>
            ) : draft ? (
              <>
                <div className="banner banner-info" role="note">
                  <Icon name="file" />
                  <div className="banner-body">
                    <p>
                      This is a draft. It fills in as Iris works, and figures show the original crop
                      until they're redrawn.
                    </p>
                  </div>
                </div>
                <NotesView
                  sessionId={session.id}
                  title={draft.title}
                  markdown={draft.markdown}
                  figures={figureMeta}
                  hideTitle={draft.title.trim() === session.title.trim()}
                  onOutline={setOutline}
                />
              </>
            ) : (
              <NotesPlaceholder kind={ui.kind} run={run.notes} />
            )}
          </TabPanel>

          <TabPanel id="transcript" idPrefix="session" active={tab === 'transcript'}>
            {segments.length ? (
              <TranscriptList segments={segments} />
            ) : (
              <div className="placeholder">
                {finished ? (
                  <h2>No speech was transcribed</h2>
                ) : (
                  <>
                    <h2>No transcript yet</h2>
                    <p>Lines appear here as each part of the recording is transcribed.</p>
                  </>
                )}
              </div>
            )}
          </TabPanel>

          <TabPanel id="figures" idPrefix="session" active={tab === 'figures'}>
            {figures.length ? (
              <FiguresGrid sessionId={session.id} figures={figures} />
            ) : (
              <div className="placeholder">
                {finished ? (
                  <>
                    <h2>No figures in this session</h2>
                    <p>Iris found nothing on screen worth redrawing.</p>
                  </>
                ) : (
                  <>
                    <h2>No figures yet</h2>
                    <p>Figures appear here once Iris has read the screen, and fill in as they're redrawn.</p>
                  </>
                )}
              </div>
            )}
          </TabPanel>
        </div>

        <ProgressRail
          run={run}
          finished={finished}
          hasEvents={stream.eventCount > 0}
          currentStage={session.current_stage}
          outline={notes || draft ? outline : []}
          stream={stream.status}
          error={session.error ?? run.error}
          showProgress={!finished || stream.eventCount > 0}
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

function NotesPlaceholder({ kind, run }: { kind: string; run: { completed: number; total: number } | null }) {
  if (kind === 'draft' && run) {
    return (
      <div className="placeholder">
        <h2>
          Writing notes: {run.completed} of {run.total} sections done
        </h2>
        <p>The notes appear here when they're put together with their figures.</p>
      </div>
    )
  }
  if (kind === 'failed' || kind === 'stalled') {
    return (
      <div className="placeholder">
        <h2>No notes yet</h2>
        <p>Processing stopped before the notes were written. Use {kind === 'failed' ? 'Try again' : 'Resume'} above.</p>
      </div>
    )
  }
  if (kind === 'recording-orphan') {
    return (
      <div className="placeholder">
        <h2>No notes yet</h2>
        <p>Notes are made after the recording is finished.</p>
      </div>
    )
  }
  if (kind === 'finished') {
    return (
      <div className="placeholder">
        <h2>The notes couldn't be loaded</h2>
        <p>Reload the page to try again.</p>
      </div>
    )
  }
  return (
    <div className="placeholder">
      <h2>Notes appear here as they're written</h2>
      <p>A one-hour recording usually takes a few minutes. The transcript and figures fill in first.</p>
    </div>
  )
}
