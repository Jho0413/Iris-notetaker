import { createPortal } from 'react-dom'
import type { Notes, Session, Transcript } from '../api/client'
import {
  emptyRun, figureList, figureMeta, sessionLength, sessionMetaLine, transcriptSegments,
} from '../session/state'
import { NotesView } from './NotesView'

export interface Printable {
  session: Session
  notes: Notes
  transcript: Transcript | null
}

/**
 * A session's notes as printed (Export PDF): its title, meta line and notes. Rendered only while a
 * PDF is made, at the end of <body> and invisible on screen, so exporting changes nothing the user
 * sees; while it is there, printing shows it alone (index.css, `.print-sheet`).
 */
export function PrintSheet({ printable }: { printable: Printable }) {
  const { session, notes, transcript } = printable
  // A finished session: everything shown comes from its notes and transcript, no live run.
  const run = emptyRun()
  const length = sessionLength(session, transcriptSegments(run, transcript))
  return createPortal(
    <div className="print-sheet" aria-hidden="true">
      <div className="session-head">
        <div className="titles">
          <h1>{session.title}</h1>
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
        figures={figureMeta(figureList(run, notes))}
        hideTitle={notes.title.trim() === session.title.trim()}
      />
    </div>,
    document.body,
  )
}
