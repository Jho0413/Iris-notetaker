import 'katex/dist/katex.min.css'
import type { Element } from 'hast'
import { useEffect, useMemo, useRef } from 'react'
import Markdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { sessionFileUrl } from '../api/client'
import { rehypeNotes } from '../notes/rehypeNotes'
import { formatClock, type Provenance } from '../session/state'
import { ProvenanceBadge } from './FiguresGrid'

export interface FigureMeta {
  t?: number
  provenance?: Provenance
}

export interface OutlineItem {
  id: string
  text: string
}

const REMARK = [remarkGfm, remarkMath]
const REHYPE = [rehypeKatex, rehypeNotes]

/**
 * Renders notes markdown without raw HTML; image paths like "visuals/v1.svg" are served from the
 * session. Section headings get ids and are reported for the rail's "On this page" list.
 */
export function NotesView(props: {
  sessionId: string
  title: string
  markdown: string
  /** Per image path (relative to the session): when it was shown and how it was produced. */
  figures?: ReadonlyMap<string, FigureMeta>
  /** Hide the notes' own title when the page heading already says the same. */
  hideTitle?: boolean
  onOutline?: (items: OutlineItem[]) => void
}) {
  const ref = useRef<HTMLElement>(null)
  const { onOutline, figures, sessionId } = props

  useEffect(() => {
    const root = ref.current
    if (!root) return
    const used = new Map<string, number>()
    const items = [...root.querySelectorAll('h2')].map((h) => {
      const text = h.textContent?.trim() ?? ''
      const base = `s-${text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'section'}`
      const n = (used.get(base) ?? 0) + 1
      used.set(base, n)
      h.id = n > 1 ? `${base}-${n}` : base
      return { id: h.id, text }
    })
    onOutline?.(items)
  }, [props.markdown, onOutline])

  // Under each caption: when the figure was shown, and how it was produced.
  // Figures come from rehypeNotes; their src may be relative or already resolved.
  const components = useMemo<Components>(() => {
    const bySrc = new Map<string, FigureMeta>()
    for (const [path, meta] of figures ?? []) {
      bySrc.set(path, meta)
      bySrc.set(sessionFileUrl(sessionId, path), meta)
    }
    return {
      figure({ node, children, className }) {
        const img = node?.children.find((c): c is Element => c.type === 'element' && c.tagName === 'img')
        const meta = bySrc.get(String(img?.properties.src ?? ''))
        return (
          <figure className={className}>
            {children}
            {meta && (meta.t !== undefined || meta.provenance) && (
              <span className="figure-meta">
                {meta.t !== undefined && <span className="num">Shown at {formatClock(meta.t)}</span>}
                <ProvenanceBadge kind={meta.provenance} />
              </span>
            )}
          </figure>
        )
      },
    }
  }, [figures, sessionId])

  return (
    <article className="notes" ref={ref}>
      {!props.hideTitle && <h1>{props.title}</h1>}
      <Markdown
        remarkPlugins={REMARK}
        rehypePlugins={REHYPE}
        components={components}
        urlTransform={(url) =>
          /^(https?:|data:|\/)/.test(url) ? url : sessionFileUrl(sessionId, url)
        }
      >
        {props.markdown}
      </Markdown>
    </article>
  )
}
