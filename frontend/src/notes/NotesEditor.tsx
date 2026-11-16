import { NodeViewWrapper, ReactNodeViewRenderer, EditorContent, useEditor, type ReactNodeViewProps } from '@tiptap/react'
import type { Node as PMNode } from '@tiptap/pm/model'
import { NodeSelection, TextSelection } from '@tiptap/pm/state'
import type { EditorView } from '@tiptap/pm/view'
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { api, type Notes } from '../api/client'
import { act } from '../api/request'
import {
  ADDABLE, addBelow, blockAt, blockOfSelection, canLand, isContainer, KIND_LABELS, kindOf, makeMaths, moveBy,
  moveTo, remove, setColour, turnInto, turnIntoChoices, usedFigures, within, type Addable, type Block, type Kind,
  type Target,
} from './editor/blocks'
import { Figure, notesExtensions } from './editor/extensions'
import { landingSpots, nearestSpot, type Measured, type Spot } from './editor/landing'
import { toDoc, toMarkdown } from './editor/markdown'
import { editorPlugins, flashKey, mathWithViews, selectedBlock, selectedBlockKey, type OpenMath } from './editor/plugins'
import type { Colour } from './format'
import { NOTES_EDIT, saveRefused, UNREACHABLE } from '../lib/messages'
import type { FigureMeta } from '../lib/notes'
import { Banner } from '../components/Banner'
import { ConfirmDialog, LeaveWithoutSavingDialog } from '../components/ConfirmDialog'
import { MathEditor, MenuItem, Popover, Swatches } from './EditorMenu'
import { Icon, type IconName } from '../components/Icon'
import { useAskBeforeLeaving } from '../hooks/useAskBeforeLeaving'
import { FigureView } from './NotesView'

type Visual = Notes['visuals'][number]

interface FigureSource {
  sessionId: string
  visuals: ReadonlyMap<string, Visual>
  figures?: ReadonlyMap<string, FigureMeta>
}
const FigureContext = createContext<FigureSource | null>(null)

function FigureNode({ node }: ReactNodeViewProps) {
  const source = useContext(FigureContext)
  return (
    <NodeViewWrapper className="figure-node" data-drag-handle="">
      {source && <FigureView id={node.attrs.id as string} {...source} />}
    </NodeViewWrapper>
  )
}

const KIND_ICONS: Partial<Record<Kind | Addable, IconName>> = {
  text: 'text', heading: 'heading', box: 'box', step: 'stepIcon', maths: 'sigma',
}

function blockFromDom(view: EditorView, target: EventTarget | null): number | null {
  if (!(target instanceof Node) || !view.dom.contains(target)) return null
  let parent: PMNode = view.state.doc
  let base = 0
  let found: number | null = null
  for (;;) {
    let next: { node: PMNode; pos: number } | null = null
    let offset = base
    for (let i = 0; i < parent.childCount; i++) {
      const child = parent.child(i)
      const dom = view.nodeDOM(offset)
      if (dom && dom.contains(target)) {
        next = { node: child, pos: offset }
        break
      }
      offset += child.nodeSize
    }
    if (!next || next.node.type.name === 'blockTitle') return found
    found = next.pos
    if (!isContainer(next.node)) return found
    parent = next.node
    base = next.pos + 1
  }
}

function label(block: Block): string {
  const kind = KIND_LABELS[kindOf(block.node)]
  const title = isContainer(block.node) ? block.node.child(0).textContent : block.node.type.name === 'heading' ? block.node.textContent : ''
  return title ? `${kind} · ${title.length > 30 ? `${title.slice(0, 30)}…` : title}` : kind
}

type Menu = { kind: 'colour' | 'turn' | 'add'; anchor: HTMLElement } | { kind: 'math'; anchor: HTMLElement; pos: number }

export function NotesEditor(props: {
  notes: Notes
  figures?: ReadonlyMap<string, FigureMeta>
  onSaved: (notes: Notes) => void
  onClose: () => void
}) {
  const { notes } = props
  const visuals = useMemo(() => new Map(notes.visuals.map((v) => [v.id, v])), [notes.visuals])
  const figureSource = useMemo(
    () => ({ sessionId: notes.session_id, visuals, figures: props.figures }),
    [notes.session_id, visuals, props.figures],
  )
  const [menu, setMenu] = useState<Menu | null>(null)
  const openMath: OpenMath = (pos, anchor) => setMenu({ kind: 'math', anchor, pos })
  const [version, setVersion] = useState(0)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmDiscard, setConfirmDiscard] = useState(false)
  const initial = useRef<PMNode | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const toolbarRef = useRef<HTMLDivElement>(null)

  const editor = useEditor({
    extensions: [
      ...notesExtensions().filter((e) => !['figure', 'inlineMath', 'blockMath'].includes(e.name)),
      Figure.extend({ addNodeView: () => ReactNodeViewRenderer(FigureNode) }),
      ...mathWithViews(openMath),
      ...editorPlugins,
    ],
    content: toDoc(notes.markdown),
    editorProps: {
      attributes: { class: 'notes notes-editable', 'aria-label': 'Notes', spellcheck: 'true' },
      handleKeyDown(view, event) {
        const sel = view.state.selection
        if (event.key === 'Enter' && sel instanceof NodeSelection && /math/i.test(sel.node.type.name)) {
          const dom = view.nodeDOM(sel.from)
          if (dom instanceof HTMLElement) openMath(sel.from, dom)
          return true
        }
        if (event.key === 'Escape') {
          toolbarRef.current?.querySelector<HTMLElement>('button')?.focus()
          return true
        }
        return false
      },
      handleDOMEvents: {
        pointerdown(view, event) {
          const pos = blockFromDom(view, event.target)
          if (pos !== null && pos !== selectedBlock(view.state)) {
            view.dispatch(view.state.tr.setMeta(selectedBlockKey, pos))
          }
          return false
        },
      },
    },
    onTransaction({ editor, transaction }) {
      initial.current ??= transaction.before
      if (transaction.selectionSet) {
        queueMicrotask(() => {
          const state = editor.state
          const current = selectedBlock(state)
          const at = blockOfSelection(state.selection)
          if (at !== null && (current === null || !within(state.doc, at, current))) {
            editor.view.dispatch(state.tr.setMeta(selectedBlockKey, at))
          }
        })
      }
      if (transaction.docChanged) setDirty(!transaction.doc.eq(initial.current))
      setVersion((v) => v + 1)
    },
  })

  const dispatch = useCallback(
    (tr: import('@tiptap/pm/state').Transaction | null, select?: number) => {
      if (!editor || !tr) return
      if (select !== undefined) tr.setMeta(selectedBlockKey, select)
      editor.view.dispatch(tr)
    },
    [editor],
  )

  const blocker = useAskBeforeLeaving(dirty)

  async function save() {
    if (!editor) return
    const markdown = toMarkdown(editor.getJSON())
    setSaving(true)
    setError(null)
    const { data, error: failure, status } = await act(
      api.PUT('/api/sessions/{session_id}/notes/edits', {
        params: { path: { session_id: notes.session_id } },
        body: { markdown, based_on: notes.generated_hash ?? '' },
      }),
    )
    setSaving(false)
    if (data) return props.onSaved(data)
    const detail = (failure as { detail?: unknown } | undefined)?.detail
    setError(
      status === 0
        ? UNREACHABLE
        : status === 409
          ? NOTES_EDIT.saveWhileProcessing
          : status === 422 && typeof detail === 'string'
            ? saveRefused(detail)
            : NOTES_EDIT.saveFailed,
    )
  }

  function close() {
    if (dirty) setConfirmDiscard(true)
    else props.onClose()
  }

  const state = editor?.state
  const selected = state ? selectedBlock(state) : null
  const block = state && selected !== null ? blockAt(state.doc, selected) : null
  const [place, setPlace] = useState<{ top: number; right: number } | null>(null)
  useEffect(() => {
    const measure = () => {
      const dom = editor && selected !== null ? editor.view.nodeDOM(selected) : null
      const wrap = wrapRef.current
      if (!(dom instanceof HTMLElement) || !wrap) return setPlace(null)
      const r = dom.getBoundingClientRect()
      const w = wrap.getBoundingClientRect()
      setPlace({ top: r.top - w.top, right: Math.max(0, w.right - r.right) })
    }
    measure()
    window.addEventListener('resize', measure)
    return () => window.removeEventListener('resize', measure)
  }, [editor, selected, version])

  const [bubble, setBubble] = useState<{ top: number; left: number } | null>(null)
  useEffect(() => {
    const sel = editor?.state.selection
    const wrap = wrapRef.current
    if (!editor || !sel || !wrap || sel.empty || !(sel instanceof TextSelection) || !sel.$from.parent.inlineContent || sel.$from.parent.type.name === 'codeBlock' || !editor.isFocused) {
      setBubble(null)
      return
    }
    const start = editor.view.coordsAtPos(sel.from)
    const w = wrap.getBoundingClientRect()
    setBubble({ top: start.top - w.top - 42, left: Math.max(0, start.left - w.left) })
  }, [editor, version])

  const drag = useRef<{
    pos: number
    x: number
    y: number
    started: boolean
    spots: Spot[]
    target: Spot | null
    chip?: HTMLElement
    line?: HTMLElement
    raf: number
    px: number
    py: number
  } | null>(null)
  const suppressClick = useRef(false)

  const measure = useCallback((view: EditorView, from: number): Spot[] => {
    const rect = (pos: number) => {
      const dom = view.nodeDOM(pos)
      return dom instanceof Element ? dom.getBoundingClientRect() : undefined
    }
    const page = (r: DOMRect) => ({ top: r.top + window.scrollY, bottom: r.bottom + window.scrollY })
    const blocks: Measured[] = []
    view.state.doc.forEach((node, pos) => {
      const r = rect(pos)
      if (!r) return
      const m: Measured = { pos, ...page(r) }
      if (isContainer(node)) {
        const children: Measured[] = []
        let left = Infinity
        let right = -Infinity
        node.forEach((_child, offset, i) => {
          const cr = rect(pos + 1 + offset)
          if (!cr || i === 0) return
          children.push({ pos: pos + 1 + offset, ...page(cr) })
          left = Math.min(left, cr.left)
          right = Math.max(right, cr.right)
        })
        if (children.length) m.body = { left: left + window.scrollX, right: right + window.scrollX, bottom: page(r).bottom - 8, children }
      }
      blocks.push(m)
    })
    const col = view.dom.getBoundingClientRect()
    return landingSpots(blocks, { left: col.left + window.scrollX, right: col.right + window.scrollX }, (t: Target) => canLand(view.state.doc, from, t))
  }, [])

  const endDrag = useCallback(() => {
    const d = drag.current
    if (!d) return
    cancelAnimationFrame(d.raf)
    d.chip?.remove()
    d.line?.remove()
    document.body.classList.remove('is-dragging')
    drag.current = null
  }, [])

  useEffect(() => {
    if (!editor) return
    const frame = () => {
      const d = drag.current
      if (!d?.started) return
      d.raf = 0
      d.chip!.style.left = `${d.px}px`
      d.chip!.style.top = `${d.py}px`
      const dy = d.py < 120 ? -14 : d.py > window.innerHeight - 60 ? 14 : 0
      if (dy) {
        window.scrollBy(0, dy)
        d.raf = requestAnimationFrame(frame)
      }
      d.target = nearestSpot(d.spots, d.px + window.scrollX, d.py + window.scrollY)
      const line = d.line!
      if (d.target) {
        line.style.display = 'block'
        line.style.top = `${d.target.y - 1}px`
        line.style.left = `${d.target.left}px`
        line.style.width = `${d.target.right - d.target.left}px`
      } else line.style.display = 'none'
    }
    const move = (e: PointerEvent) => {
      const d = drag.current
      if (!d) return
      if (!d.started) {
        if (Math.hypot(e.clientX - d.x, e.clientY - d.y) < 5) return
        const b = blockAt(editor.state.doc, d.pos)
        if (!b) return endDrag()
        setMenu(null)
        window.getSelection()?.removeAllRanges()
        d.spots = measure(editor.view, d.pos)
        d.chip = Object.assign(document.createElement('div'), { className: 'drag-chip', textContent: label(b) })
        d.line = Object.assign(document.createElement('div'), { className: 'drop-line' })
        document.body.append(d.chip, d.line)
        document.body.classList.add('is-dragging')
        d.started = true
      }
      e.preventDefault()
      d.px = e.clientX
      d.py = e.clientY
      if (!d.raf) d.raf = requestAnimationFrame(frame)
    }
    const up = () => {
      const d = drag.current
      if (!d) return
      const { started, target, pos } = d
      endDrag()
      if (!started) return
      suppressClick.current = true
      setTimeout(() => (suppressClick.current = false), 0)
      if (!target) return
      const moved = moveTo(editor.state, pos, target.target)
      if (!moved) return
      dispatch(moved.tr.setMeta(flashKey, moved.pos), moved.pos)
      const dom = editor.view.nodeDOM(moved.pos)
      if (dom instanceof HTMLElement) dom.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
      window.setTimeout(() => editor.view.dispatch(editor.state.tr.setMeta(flashKey, null)), 1000)
    }
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && drag.current?.started) {
        e.preventDefault()
        endDrag()
      }
    }
    document.addEventListener('pointermove', move, { passive: false })
    document.addEventListener('pointerup', up)
    document.addEventListener('pointercancel', endDrag)
    document.addEventListener('keydown', key)
    return () => {
      document.removeEventListener('pointermove', move)
      document.removeEventListener('pointerup', up)
      document.removeEventListener('pointercancel', endDrag)
      document.removeEventListener('keydown', key)
      endDrag()
    }
  }, [editor, measure, endDrag, dispatch])

  if (!editor || !state) return null

  const used = usedFigures(state.doc)
  const parent = block?.parent != null ? blockAt(state.doc, block.parent) : null
  const choices = block ? turnIntoChoices(block) : []
  const colour = block && isContainer(block.node) ? (block.node.attrs.color as Colour) : null
  const kind = block ? kindOf(block.node) : null
  const status = saving ? 'Saving…' : dirty ? 'Unsaved changes' : 'No changes yet'

  const apply = (make: () => { tr: import('@tiptap/pm/state').Transaction; pos: number } | null) => {
    const r = make()
    if (r) dispatch(r.tr, r.pos)
    setMenu(null)
  }

  const toolbar: ReactNode = block && place && (
    <div
      ref={toolbarRef}
      className="blk-toolbar"
      role="toolbar"
      aria-label={`${KIND_LABELS[kind!]} options`}
      style={{ top: place.top, right: place.right }}
    >
      {parent && (
        <>
          <button
            type="button"
            className="tb-btn tb-parent"
            title={`Select the ${KIND_LABELS[kindOf(parent.node)].toLowerCase()} around it, to move or change all of it`}
            onClick={() => dispatch(editor.state.tr.setMeta(selectedBlockKey, parent.pos))}
          >
            Inside {KIND_LABELS[kindOf(parent.node)].toLowerCase()} <Icon name="up" />
          </button>
          <span className="sep" />
        </>
      )}
      <button
        type="button"
        className="tb-btn tb-drag"
        aria-label={`Drag to move this ${KIND_LABELS[kind!].toLowerCase()}`}
        title="Drag to move"
        onPointerDown={(e) => {
          if (e.button !== 0) return
          e.preventDefault()
          drag.current = { pos: block.pos, x: e.clientX, y: e.clientY, started: false, spots: [], target: null, raf: 0, px: e.clientX, py: e.clientY }
        }}
      >
        <Icon name="grip" />
      </button>
      {colour && (
        <button
          type="button"
          className={`tb-btn c-${colour}`}
          aria-haspopup="menu"
          aria-expanded={menu?.kind === 'colour'}
          aria-label={`Colour: ${colour}`}
          onClick={(e) => setMenu({ kind: 'colour', anchor: e.currentTarget })}
        >
          <span className="dot" aria-hidden="true" />
          <span className="tb-word">{colour}</span>
        </button>
      )}
      <button
        type="button"
        className="tb-btn"
        aria-haspopup="menu"
        aria-expanded={menu?.kind === 'turn'}
        aria-label={`${KIND_LABELS[kind!]}: turn into`}
        disabled={!choices.length}
        onClick={(e) => setMenu({ kind: 'turn', anchor: e.currentTarget })}
      >
        {KIND_LABELS[kind!]}
      </button>
      <span className="sep" />
      <button type="button" className="tb-btn" aria-label="Move up" title="Move up" onClick={() => apply(() => moveBy(editor.state, block.pos, -1))}>
        <Icon name="up" />
      </button>
      <button type="button" className="tb-btn" aria-label="Move down" title="Move down" onClick={() => apply(() => moveBy(editor.state, block.pos, 1))}>
        <Icon name="down" />
      </button>
      <button
        type="button"
        className="tb-btn"
        aria-label="Add a block below"
        title="Add below"
        aria-haspopup="menu"
        aria-expanded={menu?.kind === 'add'}
        onClick={(e) => setMenu({ kind: 'add', anchor: e.currentTarget })}
      >
        <Icon name="plus" />
      </button>
      <button
        type="button"
        className="tb-btn"
        aria-label={`Delete this ${KIND_LABELS[kind!].toLowerCase()}`}
        title="Delete"
        onClick={() => dispatch(remove(editor.state, block.pos), undefined)}
      >
        <Icon name="trash" />
      </button>
    </div>
  )

  const closeMenu = () => setMenu(null)

  return (
    <FigureContext.Provider value={figureSource}>
      <div className="edit-strip" role="region" aria-label="Editing notes">
        <p>
          <strong>Editing notes</strong> <span className="hint">· click a block for its options</span>
        </p>
        <span className="edit-status" role="status">{status}</span>
        <button type="button" className="btn btn-sm" onClick={close} disabled={saving}>
          {dirty ? 'Discard' : 'Close'}
        </button>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => void save()} disabled={!dirty || saving}>
          Save
        </button>
      </div>
      {error && <Banner tone="error" className="edit-error">{error}</Banner>}

      <div className="notes-editor" ref={wrapRef} onClickCapture={(e) => suppressClick.current && e.stopPropagation()}>
        <EditorContent editor={editor} />
        {toolbar}
        {bubble && (
          <div className="bubble" role="toolbar" aria-label="Format text" style={bubble} onMouseDown={(e) => e.preventDefault()}>
            <button type="button" aria-label="Bold" aria-pressed={editor.isActive('bold')} onClick={() => editor.chain().focus().toggleBold().run()}>B</button>
            <button type="button" className="i" aria-label="Italic" aria-pressed={editor.isActive('italic')} onClick={() => editor.chain().focus().toggleItalic().run()}>I</button>
            <button type="button" className="s" aria-label="Strikethrough" aria-pressed={editor.isActive('strike')} onClick={() => editor.chain().focus().toggleStrike().run()}>S</button>
            <button type="button" aria-label="Make maths" onClick={() => dispatch(makeMaths(editor.state))}>
              <Icon name="sigma" />
            </button>
          </div>
        )}
      </div>

      {menu?.kind === 'colour' && block && colour && (
        <Popover anchor={menu.anchor} label="Colour" onClose={closeMenu}>
          <h3>Colour</h3>
          <Swatches value={colour} onPick={(c) => { dispatch(setColour(editor.state, block.pos, c)); closeMenu(); menu.anchor.focus() }} />
        </Popover>
      )}
      {menu?.kind === 'turn' && block && (
        <Popover anchor={menu.anchor} label="Turn into" onClose={closeMenu}>
          <h3>Turn into</h3>
          {choices.map((k) => (
            <MenuItem key={k} icon={<Icon name={KIND_ICONS[k]!} />} onSelect={() => { dispatch(turnInto(editor.state, block.pos, k), block.pos); closeMenu(); menu.anchor.focus() }}>
              {KIND_LABELS[k]}
            </MenuItem>
          ))}
        </Popover>
      )}
      {menu?.kind === 'add' && block && (
        <Popover anchor={menu.anchor} label="Add below" onClose={closeMenu}>
          <h3>Add below</h3>
          {ADDABLE.map((k) => (
            <MenuItem
              key={k}
              icon={<Icon name={KIND_ICONS[k]!} />}
              onSelect={() => {
                const added = addBelow(editor.state, block.pos, k)
                if (!added) return
                dispatch(added.tr, added.pos)
                closeMenu()
                const dom = editor.view.nodeDOM(added.pos)
                if (k === 'maths' && dom instanceof HTMLElement) setMenu({ kind: 'math', anchor: dom, pos: added.pos })
                else {
                  editor.chain().focus().setTextSelection(added.pos + (k === 'box' || k === 'step' ? 2 : 1)).run()
                }
              }}
            >
              {KIND_LABELS[k]}
            </MenuItem>
          ))}
          {notes.visuals.length > 0 && (
            <>
              <div className="mi-sep" />
              <h3>Figures from this session</h3>
              {notes.visuals.map((v) => (
                <MenuItem
                  key={v.id}
                  icon={<Icon name="image" />}
                  disabled={used.has(v.id)}
                  note={used.has(v.id) ? 'Already in the notes' : undefined}
                  onSelect={() => apply(() => addBelow(editor.state, block.pos, { figure: v.id }))}
                >
                  {v.caption || 'Figure'}
                </MenuItem>
              ))}
            </>
          )}
        </Popover>
      )}
      {menu?.kind === 'math' && (
        <MathEditor
          anchor={menu.anchor}
          latex={(state.doc.nodeAt(menu.pos)?.attrs.latex as string | undefined) ?? ''}
          onClose={() => { closeMenu(); editor.commands.focus() }}
          onDone={(latex) => {
            const node = state.doc.nodeAt(menu.pos)
            if (node) dispatch(editor.state.tr.setNodeMarkup(menu.pos, undefined, { ...node.attrs, latex }))
            closeMenu()
            editor.commands.focus()
          }}
        />
      )}

      <ConfirmDialog
        open={confirmDiscard}
        title={NOTES_EDIT.discardTitle}
        body={NOTES_EDIT.discardBody}
        confirm="Discard changes"
        cancel="Keep editing"
        danger
        onConfirm={() => { setConfirmDiscard(false); props.onClose() }}
        onCancel={() => setConfirmDiscard(false)}
      />
      <LeaveWithoutSavingDialog blocker={blocker} body={NOTES_EDIT.leaveBody} />
    </FigureContext.Provider>
  )
}
