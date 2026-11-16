import type { Node as PMNode, NodeType, Schema } from '@tiptap/pm/model'
import { NodeSelection, type EditorState, type Selection, type Transaction } from '@tiptap/pm/state'
import type { Colour } from '../format.ts'
import { DEFAULT_COLOUR, type Container } from './extensions.ts'

export const KIND_LABELS = {
  text: 'Text',
  heading: 'Heading',
  box: 'Box',
  step: 'Step',
  maths: 'Maths',
  figure: 'Figure',
  list: 'List',
  checklist: 'Checklist',
  table: 'Table',
  code: 'Code',
  quote: 'Quote',
  divider: 'Divider',
} as const
export type Kind = keyof typeof KIND_LABELS
export const TURN_INTO = ['text', 'heading', 'box', 'step'] as const
export type TurnInto = (typeof TURN_INTO)[number]
export const ADDABLE = ['text', 'heading', 'box', 'step', 'maths'] as const
export type Addable = (typeof ADDABLE)[number]

const NODE_KINDS: Record<string, Kind> = {
  paragraph: 'text',
  heading: 'heading',
  box: 'box',
  step: 'step',
  blockMath: 'maths',
  figure: 'figure',
  bulletList: 'list',
  orderedList: 'list',
  taskList: 'checklist',
  table: 'table',
  codeBlock: 'code',
  blockquote: 'quote',
  horizontalRule: 'divider',
}

export const kindOf = (node: PMNode): Kind => NODE_KINDS[node.type.name] ?? 'text'
export const isContainer = (node: PMNode | null | undefined): boolean =>
  node?.type.name === 'box' || node?.type.name === 'step'
const holdsBlocks = (node: PMNode) => node.type.name === 'doc' || isContainer(node)

export interface Block {
  pos: number
  node: PMNode
  depth: number
  parent: number | null
}

export function blockAt(doc: PMNode, pos: number): Block | null {
  if (pos < 0 || pos >= doc.content.size) return null
  const node = doc.nodeAt(pos)
  const $pos = doc.resolve(pos)
  if (!node || !holdsBlocks($pos.parent) || node.type.name === 'blockTitle') return null
  const parents: number[] = []
  for (let d = $pos.depth; d > 0; d--) if (isContainer($pos.node(d))) parents.push($pos.before(d))
  return { pos, node, depth: parents.length, parent: parents[0] ?? null }
}

export function blockOfSelection(selection: Selection): number | null {
  if (selection instanceof NodeSelection && holdsBlocks(selection.$from.parent)) return selection.from
  const $from = selection.$from
  for (let d = $from.depth; d > 0; d--) {
    if (!holdsBlocks($from.node(d - 1))) continue
    return $from.node(d).type.name === 'blockTitle' ? $from.before(d - 1) : $from.before(d)
  }
  return null
}

export function within(doc: PMNode, inner: number, outer: number): boolean {
  const node = doc.nodeAt(outer)
  return !!node && inner >= outer && inner < outer + node.nodeSize
}

const MAX_DEPTH = 1

export function turnIntoChoices(block: Block): TurnInto[] {
  const kind = kindOf(block.node)
  return TURN_INTO.filter((k) => {
    if (k === kind) return false
    if ((k === 'text' || k === 'heading') && !['text', 'heading', 'box', 'step'].includes(kind)) return false
    if (k === 'step' && block.depth > 0) return false
    if ((k === 'box' || k === 'step') && !isContainer(block.node) && block.depth > MAX_DEPTH) return false
    return true
  })
}

function container(schema: Schema, name: Container, color: string, title: PMNode[], body: PMNode[]): PMNode {
  const type = schema.nodes[name]
  return type.create({ color }, [
    schema.nodes.blockTitle.create(null, title),
    ...(body.length ? body : [schema.nodes.paragraph.create()]),
  ])
}

const children = (node: PMNode) => {
  const out: PMNode[] = []
  node.forEach((c) => out.push(c))
  return out
}

export function turnInto(state: EditorState, pos: number, kind: TurnInto): Transaction | null {
  const block = blockAt(state.doc, pos)
  if (!block || !turnIntoChoices(block).includes(kind)) return null
  const { schema } = state
  const { node } = block
  const tr = state.tr
  const end = pos + node.nodeSize
  const textType: Record<'text' | 'heading', [NodeType, Record<string, unknown> | null]> = {
    text: [schema.nodes.paragraph, null],
    heading: [schema.nodes.heading, { level: 2 }],
  }
  if (kind === 'text' || kind === 'heading') {
    const [type, attrs] = textType[kind]
    if (!isContainer(node)) return tr.setNodeMarkup(pos, type, attrs)
    const [title, ...body] = children(node)
    const head = title.content.size || kind === 'heading' ? [type.create(attrs, title.content)] : []
    return tr.replaceWith(pos, end, [...head, ...body])
  }
  if (isContainer(node)) {
    return tr.setNodeMarkup(pos, schema.nodes[kind], { color: node.attrs.color })
  }
  const wrapped = node.type.name === 'heading'
    ? container(schema, kind, DEFAULT_COLOUR[kind], [...node.content.content], [])
    : container(schema, kind, DEFAULT_COLOUR[kind], [], [node])
  return tr.replaceWith(pos, end, wrapped)
}

export function setColour(state: EditorState, pos: number, colour: Colour): Transaction | null {
  const block = blockAt(state.doc, pos)
  if (!block || !isContainer(block.node)) return null
  return state.tr.setNodeMarkup(pos, undefined, { ...block.node.attrs, color: colour })
}

export interface Target {
  parent: number | null
  index: number
}

function childPos(doc: PMNode, target: Target): number {
  const parent = target.parent === null ? doc : doc.nodeAt(target.parent)!
  let pos = target.parent === null ? 0 : target.parent + 1
  for (let i = 0; i < target.index; i++) pos += parent.child(i).nodeSize
  return pos
}

export function canLand(doc: PMNode, pos: number, target: Target): boolean {
  const node = doc.nodeAt(pos)
  if (!node) return false
  if (target.parent === null) return true
  const parent = blockAt(doc, target.parent)
  if (!parent || !isContainer(parent.node) || parent.depth > 0) return false
  if (within(doc, target.parent, pos)) return false
  if (node.type.name === 'step') return false
  let nested = false
  node.descendants((d) => {
    if (isContainer(d)) nested = true
    return !nested
  })
  return !nested
}

export function moveTo(state: EditorState, pos: number, target: Target): { tr: Transaction; pos: number } | null {
  const node = state.doc.nodeAt(pos)
  if (!node || !canLand(state.doc, pos, target)) return null
  const at = childPos(state.doc, target)
  if (at === pos || at === pos + node.nodeSize) return null
  const tr = state.tr.delete(pos, pos + node.nodeSize)
  const insert = tr.mapping.map(at, -1)
  tr.insert(insert, node)
  return { tr, pos: insert }
}

function indexIn(doc: PMNode, pos: number): number {
  return doc.resolve(pos).index()
}

export function moveBy(state: EditorState, pos: number, dir: -1 | 1): { tr: Transaction; pos: number } | null {
  const block = blockAt(state.doc, pos)
  if (!block) return null
  const parentNode = block.parent === null ? state.doc : state.doc.nodeAt(block.parent)!
  const index = indexIn(state.doc, pos)
  const first = block.parent === null ? 0 : 1
  const next = index + dir
  if (next < first || next >= parentNode.childCount) {
    if (block.parent === null) return null
    const outer = blockAt(state.doc, block.parent)!
    const at = indexIn(state.doc, outer.pos) + (dir > 0 ? 1 : 0)
    return moveTo(state, pos, { parent: outer.parent, index: at })
  }
  return moveTo(state, pos, { parent: block.parent, index: dir > 0 ? next + 1 : next })
}

export function addBelow(
  state: EditorState,
  pos: number,
  kind: Addable | { figure: string },
): { tr: Transaction; pos: number } | null {
  const block = blockAt(state.doc, pos)
  if (!block) return null
  const { schema } = state
  const node = typeof kind === 'object'
    ? schema.nodes.figure.create({ id: kind.figure })
    : kind === 'text'
      ? schema.nodes.paragraph.create()
      : kind === 'heading'
        ? schema.nodes.heading.create({ level: 2 })
        : kind === 'maths'
          ? schema.nodes.blockMath.create({ latex: '' })
          : container(schema, kind, DEFAULT_COLOUR[kind], [], [])
  let anchor = block
  const isBlock = node.type.name === 'box' || node.type.name === 'step'
  while (anchor.parent !== null && (node.type.name === 'step' || (isBlock && anchor.depth > MAX_DEPTH))) {
    anchor = blockAt(state.doc, anchor.parent)!
  }
  const at = anchor.pos + anchor.node.nodeSize
  return { tr: state.tr.insert(at, node), pos: at }
}

export function remove(state: EditorState, pos: number): Transaction | null {
  const block = blockAt(state.doc, pos)
  if (!block) return null
  return state.tr.delete(pos, pos + block.node.nodeSize)
}

export function makeMaths(state: EditorState): Transaction | null {
  const { from, to, $from, $to } = state.selection
  if (from === to || !$from.sameParent($to) || !$from.parent.inlineContent) return null
  const latex = state.doc.textBetween(from, to).trim()
  if (!latex) return null
  return state.tr.replaceSelectionWith(state.schema.nodes.inlineMath.create({ latex }))
}

export function usedFigures(doc: PMNode): Set<string> {
  const ids = new Set<string>()
  doc.descendants((n) => {
    if (n.type.name === 'figure') ids.add(n.attrs.id as string)
  })
  return ids
}

export function stepPaths(doc: PMNode): { pos: number; n: number; first: boolean; last: boolean }[] {
  const out: { pos: number; n: number; first: boolean; last: boolean }[] = []
  const walk = (parent: PMNode, start: number) => {
    let n = 0
    let pos = start
    parent.forEach((child, _offset, i) => {
      if (child.type.name === 'step') {
        n += 1
        const next = i + 1 < parent.childCount ? parent.child(i + 1) : null
        out.push({ pos, n, first: n === 1, last: next?.type.name !== 'step' })
      } else n = 0
      if (isContainer(child)) walk(child, pos + 1)
      pos += child.nodeSize
    })
  }
  walk(doc, 0)
  return out
}
