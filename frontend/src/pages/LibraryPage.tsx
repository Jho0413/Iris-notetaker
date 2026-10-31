import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Session } from '../api/client'

export function LibraryPage() {
  const [sessions, setSessions] = useState<Session[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.GET('/api/sessions').then(({ data, error }) => {
      if (error) setError('Could not reach the backend. Is it running on :8000?')
      else setSessions(data)
    })
  }, [])

  if (error) return <p className="error">{error}</p>
  if (!sessions) return <p className="muted">Loading…</p>
  if (!sessions.length)
    return (
      <p className="muted">
        No sessions yet. <Link to="/upload">Upload a recording</Link> or{' '}
        <Link to="/live">start a live session</Link>.
      </p>
    )

  return (
    <ul className="library">
      {sessions.map((s) => (
        <li key={s.id}>
          <Link to={`/sessions/${s.id}`}>{s.title}</Link>
          <span className={`badge badge-${s.status}`}>{s.status}</span>
          <span className="muted">{new Date(s.created_at).toLocaleString()}</span>
        </li>
      ))}
    </ul>
  )
}
