import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Session } from '../api/client'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { HealthBanner } from '../components/HealthBanner'
import { Icon } from '../components/Icon'
import { StatusChip } from '../components/StatusChip'
import { formatWhen, SOURCE_LABEL } from '../session/format'
import { libraryState } from '../session/state'
import { useTitle } from '../session/useTitle'

/** While anything is processing, refresh this often so status chips stay current. */
const REFRESH_MS = 4000

type Load = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; sessions: Session[] }

async function fetchSessions(): Promise<Load> {
  try {
    const { data, error } = await api.GET('/api/sessions')
    return error || !data ? { kind: 'error' } : { kind: 'ready', sessions: data }
  } catch {
    return { kind: 'error' }
  }
}

export function LibraryPage() {
  useTitle('Library')
  const [load, setLoad] = useState<Load>({ kind: 'loading' })
  const [pendingDelete, setPendingDelete] = useState<Session | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((n) => n + 1), [])

  useEffect(() => {
    let live = true
    void fetchSessions().then((next) => live && setLoad(next))
    return () => {
      live = false
    }
  }, [tick])

  const active = load.kind === 'ready' && load.sessions.some((s) => s.status === 'processing' || s.status === 'created')
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(refresh, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [active, refresh])

  async function retry(s: Session) {
    setActionError(null)
    const from = libraryState(s).fromStage
    const { error } = await api.POST('/api/sessions/{session_id}/run', {
      params: { path: { session_id: s.id }, query: from ? { from_stage: from } : {} },
    })
    if (error) setActionError(`Couldn't restart "${s.title}". It may already be processing.`)
    refresh()
  }

  async function confirmDelete() {
    if (!pendingDelete) return
    setBusy(true)
    const { error } = await api.DELETE('/api/sessions/{session_id}', {
      params: { path: { session_id: pendingDelete.id } },
    })
    setBusy(false)
    if (error) setActionError(`Couldn't delete "${pendingDelete.title}". It may be processing.`)
    setPendingDelete(null)
    refresh()
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Library</h1>
          <p className="lede">Notes that see what was shown.</p>
        </div>
      </div>
      <HealthBanner />
      {actionError && (
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <div className="banner-body">
            <p>{actionError}</p>
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setActionError(null)} aria-label="Dismiss">
            <Icon name="x" />
          </button>
        </div>
      )}

      {load.kind === 'loading' && (
        <div className="library" aria-busy="true" aria-label="Loading sessions">
          {Array.from({ length: 5 }, (_, i) => (
            <div className="library-row" key={i}>
              <div className="skeleton sk-line" style={{ width: `${60 - i * 6}%` }} />
              <div className="skeleton sk-line" style={{ width: '70%' }} />
              <div className="skeleton sk-line meta" style={{ width: '60%' }} />
              <span />
            </div>
          ))}
        </div>
      )}

      {load.kind === 'error' && (
        <div className="error-panel" role="alert">
          <h2>Can't reach the Iris backend</h2>
          <p>
            Start it with <code>make dev</code> (the API runs on port 8000), then try again.
          </p>
          <button type="button" className="btn" onClick={refresh}>
            <Icon name="retry" /> Retry
          </button>
        </div>
      )}

      {load.kind === 'ready' && load.sessions.length === 0 && (
        <div className="empty">
          <h2>No sessions yet</h2>
          <p>
            Give Iris a session where something was shown while someone talked. It writes notes with
            the slides, sketches and diagrams redrawn in the right places.
          </p>
          <div className="choices">
            <Link to="/upload" className="choice">
              <Icon name="upload" />
              <strong>Upload a recording</strong>
              <span>A video or audio file of a lecture, meeting or demo.</span>
            </Link>
            <Link to="/live" className="choice">
              <Icon name="record" />
              <strong>Record live</strong>
              <span>Share a screen or tab with your microphone, and stop when you're done.</span>
            </Link>
          </div>
        </div>
      )}

      {load.kind === 'ready' && load.sessions.length > 0 && (
        <div className="library" role="table" aria-label="Sessions">
          <div className="library-head" role="row">
            <span role="columnheader">Title</span>
            <span role="columnheader">Status</span>
            <span role="columnheader" className="meta">
              Created
            </span>
            <span role="columnheader">
              <span className="sr-only">Actions</span>
            </span>
          </div>
          {load.sessions.map((s) => {
            const state = libraryState(s)
            return (
              <div className="library-row" role="row" key={s.id}>
                <div className="title-cell" role="cell">
                  <Link to={`/sessions/${s.id}`} className="title-link">
                    {s.title || 'Untitled session'}
                  </Link>
                  <span className="sub">{SOURCE_LABEL[s.source] ?? s.source}</span>
                </div>
                <div className="status-cell" role="cell">
                  <StatusChip state={state} />
                </div>
                <div className="meta num" role="cell">
                  {formatWhen(s.created_at)}
                </div>
                <div className="row-actions" role="cell">
                  {state.kind === 'finished' && (
                    <a className="btn btn-sm" href={`/api/sessions/${s.id}/export`} download>
                      <Icon name="download" /> Export
                    </a>
                  )}
                  {state.kind === 'failed' && (
                    <button type="button" className="btn btn-sm" onClick={() => void retry(s)}>
                      <Icon name="retry" /> Try again
                    </button>
                  )}
                  {s.status !== 'processing' && s.status !== 'created' && (
                    <button
                      type="button"
                      className="btn btn-ghost btn-sm"
                      aria-label={`Delete ${s.title}`}
                      title="Delete"
                      onClick={() => setPendingDelete(s)}
                    >
                      <Icon name="trash" />
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      <ConfirmDialog
        open={pendingDelete !== null}
        title={`Delete "${pendingDelete?.title ?? ''}"?`}
        body="This removes the recording, transcript, figures and notes from this computer. It can't be undone."
        confirm="Delete session"
        danger
        busy={busy}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  )
}
