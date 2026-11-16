import { createPortal } from 'react-dom'
import type { Notes, Session, Transcript } from '../api/client'
import { sessionLength, sessionMetaLine, shownTitle } from '../lib/format'
import { figureMeta, notesFigures } from '../lib/notes'
import { NotesView } from './NotesView'

export interface Printable {
  session: Session
  notes: Notes
  transcript: Transcript | null
}

export function PrintSheet({ printable }: { printable: Printable }) {
  const { session, notes, transcript } = printable
  const length = sessionLength(session, transcript?.segments ?? [])
  return createPortal(
    <div className="print-sheet" aria-hidden="true">
      <div className="session-head">
        <div className="titles">
          <h1>{shownTitle(session.title)}</h1>
          <div className="session-meta">
            {sessionMetaLine(session, notes.session_type, length).map((part, i) => (
              <span key={i} className="num">{part}</span>
            ))}
          </div>
        </div>
      </div>
      <NotesView
        sessionId={session.id}
        title={notes.title}
        markdown={notes.markdown}
        visuals={notes.visuals}
        figures={figureMeta(notesFigures(notes))}
        hideTitle={notes.title.trim() === session.title.trim()}
      />
    </div>,
    document.body,
  )
}
