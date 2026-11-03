import 'katex/dist/katex.min.css'
import type { Element } from 'hast'
import { useEffect, useMemo, useRef } from 'react'
import Markdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { sessionFileUrl } from '../api/client'
import { rehypeNotes } from '../notes/rehypeNotes'

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
  /** Image paths (relative to the session) of figures shown as unverified original crops. */
  unverified?: ReadonlySet<string>
  onOutline?: (items: OutlineItem[]) => void
}) {
  const ref = useRef<HTMLElement>(null)
  const { onOutline, unverified, sessionId } = props

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

  // Unverified fallbacks are tagged under their caption (figures come from rehypeNotes).
  const components = useMemo<Components>(() => {
    const flagged = new Set([...(unverified ?? [])].flatMap((p) => [p, sessionFileUrl(sessionId, p)]))
    return {
      figure({ node, children, className }) {
        const img = node?.children.find((c): c is Element => c.type === 'element' && c.tagName === 'img')
        const src = String(img?.properties.src ?? '')
        return (
          <figure className={className}>
            {children}
            {flagged.has(src) && (
              <span className="badge badge-unverified figure-tag">Original crop · not verified</span>
            )}
          </figure>
        )
      },
    }
  }, [unverified, sessionId])

  return (
    <article className="notes" ref={ref}>
      <h1>{props.title}</h1>
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
