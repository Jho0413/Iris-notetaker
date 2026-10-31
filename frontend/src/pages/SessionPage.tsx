import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, type Notes, type Session } from '../api/client'
import { useSessionEvents } from '../api/events'
import { NotesView } from '../components/NotesView'
import { StageProgress } from '../components/StageProgress'

export function SessionPage() {
  const { id } = useParams<{ id: string }>()
  const events = useSessionEvents(id)
  const [session, setSession] = useState<Session | null>(null)
  const [notes, setNotes] = useState<Notes | null>(null)

  // Refetch on every event; cheap for a local single-user backend.
  useEffect(() => {
    if (!id) return
    const params = { path: { session_id: id } }
    api.GET('/api/sessions/{session_id}', { params }).then(({ data }) => data && setSession(data))
    api.GET('/api/sessions/{session_id}/notes', { params }).then(({ data }) => data && setNotes(data))
  }, [id, events.length])

  async function rerun() {
    if (!id) return
    await api.POST('/api/sessions/{session_id}/run', { params: { path: { session_id: id } } })
  }

  if (!session) return <p className="muted">Loading…</p>

  return (
    <div className="session">
      <header className="session-header">
        <h2>{session.title}</h2>
        <span className={`badge badge-${session.status}`}>{session.status}</span>
        {notes && (
          <a className="button" href={`/api/sessions/${session.id}/export`}>
            Download
          </a>
        )}
        {session.status === 'failed' && <button onClick={rerun}>Retry</button>}
      </header>
      <StageProgress events={events} />
      {session.error && <p className="error">{session.error}</p>}
      {notes ? <NotesView notes={notes} /> : <p className="muted">Notes will appear here.</p>}
      {/* TODO: stream partial results (transcript chunks, moments, draft notes) as they arrive */}
    </div>
  )
}
