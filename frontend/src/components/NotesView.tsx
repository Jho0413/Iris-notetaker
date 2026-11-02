import 'katex/dist/katex.min.css'
import Markdown from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { sessionFileUrl, type Notes } from '../api/client'
import { rehypeNotes } from '../notes/rehypeNotes'

/** Renders notes markdown; image paths like "visuals/v1.svg" are served from the session. */
export function NotesView({ notes }: { notes: Notes }) {
  return (
    <article className="notes">
      <header className="notes-header">
        <span className="notes-kind">{notes.session_type}</span>
        <h1>{notes.title}</h1>
      </header>
      <Markdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, rehypeNotes]}
        urlTransform={(url) =>
          /^(https?:|data:|\/)/.test(url) ? url : sessionFileUrl(notes.session_id, url)
        }
      >
        {notes.markdown}
      </Markdown>
    </article>
  )
}
