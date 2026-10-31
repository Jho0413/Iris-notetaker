import Markdown from 'react-markdown'
import { sessionFileUrl, type Notes } from '../api/client'

/** Renders notes markdown; image paths like "visuals/v1.jpg" are served from the session. */
export function NotesView({ notes }: { notes: Notes }) {
  return (
    <article className="notes">
      <h1>{notes.title}</h1>
      <p className="muted">{notes.session_type}</p>
      <Markdown
        urlTransform={(url) =>
          /^(https?:|data:|\/)/.test(url) ? url : sessionFileUrl(notes.session_id, url)
        }
      >
        {notes.markdown}
      </Markdown>
    </article>
  )
}
