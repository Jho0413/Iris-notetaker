import 'katex/dist/katex.min.css'
import { Fragment, useEffect, useMemo, useRef } from 'react'
import Markdown, { type Components } from 'react-markdown'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import { sessionFileUrl, type Notes } from '../api/client'
import {
  FORMAT_MESSAGES, parseNotes, withPaths, type BlockNode, type NotesNode, type StepPath,
} from './format'
import { formatClock } from '../lib/format'
import type { FigureMeta, OutlineItem } from '../lib/notes'
import { ProvenanceBadge } from './FiguresGrid'

type Visual = Notes['visuals'][number]

const REMARK = [remarkGfm, remarkMath]
const REHYPE = [rehypeKatex]

// Figures come only from the session, by id: a Markdown image is never loaded.
const COMPONENTS: Components = {
  img: () => <span className="blocked">{FORMAT_MESSAGES.outsideImage}</span>,
}
// A title is inline Markdown: its paragraph is unwrapped into the block's heading.
const TITLE_COMPONENTS: Components = { ...COMPONENTS, p: ({ children }) => <>{children}</> }

function Md({ text, title }: { text: string; title?: boolean }) {
  return (
    <Markdown
      remarkPlugins={REMARK}
      rehypePlugins={REHYPE}
      components={title ? TITLE_COMPONENTS : COMPONENTS}
      urlTransform={(url) => (/^(https?:|mailto:|#)/.test(url) ? url : '')}
    >
      {text}
    </Markdown>
  )
}

interface DrawProps {
  sessionId: string
  visuals: ReadonlyMap<string, Visual>
  figures?: ReadonlyMap<string, FigureMeta>
}

export function FigureView({ id, sessionId, visuals, figures }: DrawProps & { id: string }) {
  const visual = visuals.get(id)
  if (!visual) return <p className="blk-problem-note">{FORMAT_MESSAGES.unknownFigure(id)}</p>
  const meta = figures?.get(id)
  return (
    <figure className="figure" data-figure={id}>
      <img src={sessionFileUrl(sessionId, visual.path)} alt={visual.caption} />
      <figcaption>{visual.caption}</figcaption>
      {meta && (meta.t !== undefined || meta.provenance) && (
        <span className="figure-meta">
          {meta.t !== undefined && <span className="num">Shown at {formatClock(meta.t)}</span>}
          <ProvenanceBadge kind={meta.provenance} />
        </span>
      )}
    </figure>
  )
}

function Problems({ block }: { block: BlockNode }) {
  const notes = [
    !block.closed && FORMAT_MESSAGES.unclosed,
    block.badColour !== null && FORMAT_MESSAGES.unknownColour(block.badColour),
    block.command !== 'box' && block.command !== 'step' && FORMAT_MESSAGES.unknownCommand(block.command),
  ].filter((n): n is string => !!n)
  return notes.map((n) => <p key={n} className="blk-problem-note">{n}</p>)
}

function BlockView({ block, ...props }: DrawProps & { block: BlockNode }) {
  if (block.tooDeep) {
    return (
      <>
        <p className="blk-problem-note">{FORMAT_MESSAGES.tooDeep}</p>
        {block.title && <p><strong><Md text={block.title} title /></strong></p>}
        <Nodes nodes={block.children} {...props} />
      </>
    )
  }
  const broken = !block.closed || (block.command !== 'box' && block.command !== 'step')
  return (
    <section className={`blk${block.command === 'step' ? ' blk-step' : ''} c-${block.colour}${broken ? ' blk-broken' : ''}`}>
      {block.title && <p className="blk-head"><Md text={block.title} title /></p>}
      <Nodes nodes={block.children} {...props} />
      <Problems block={block} />
    </section>
  )
}

function PathView({ path, ...props }: DrawProps & { path: StepPath }) {
  return (
    <ol className="step-run">
      {path.steps.map((step, i) => (
        <li key={i} className={`run-item c-${step.colour}`} data-n={i + 1}>
          <span className="sr-only">Step {i + 1}: </span>
          <BlockView block={step} {...props} />
        </li>
      ))}
    </ol>
  )
}

function Nodes({ nodes, ...props }: DrawProps & { nodes: readonly NotesNode[] }) {
  return withPaths(nodes).map((node, i) => (
    <Fragment key={i}>
      {node.kind === 'markdown' && <Md text={node.text} />}
      {node.kind === 'figure' && <FigureView id={node.id} {...props} />}
      {node.kind === 'block' && <BlockView block={node} {...props} />}
      {node.kind === 'path' && <PathView path={node} {...props} />}
    </Fragment>
  ))
}

/**
 * Renders notes (iris notes format: Markdown with boxes, steps and figures by id) without raw HTML.
 * Section headings get ids and are reported for the rail's "On this page" list.
 */
export function NotesView(props: {
  sessionId: string
  title: string
  markdown: string
  visuals: readonly Visual[]
  /** Per figure id: when it was shown and how it was produced. */
  figures?: ReadonlyMap<string, FigureMeta>
  /** Hide the notes' own title when the page heading already says the same. */
  hideTitle?: boolean
  onOutline?: (items: OutlineItem[]) => void
}) {
  const ref = useRef<HTMLElement>(null)
  const { onOutline } = props
  const nodes = useMemo(() => parseNotes(props.markdown), [props.markdown])
  const visuals = useMemo(() => new Map(props.visuals.map((v) => [v.id, v])), [props.visuals])

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

  return (
    <article className="notes" ref={ref}>
      {!props.hideTitle && <h1>{props.title}</h1>}
      <Nodes nodes={nodes} sessionId={props.sessionId} visuals={visuals} figures={props.figures} />
    </article>
  )
}
