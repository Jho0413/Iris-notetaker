import { Extension } from '@tiptap/core'
import { Placeholder } from '@tiptap/extensions'
import { Plugin, PluginKey, type EditorState } from '@tiptap/pm/state'
import { Decoration, DecorationSet } from '@tiptap/pm/view'
import katex from 'katex'
import { BlockMath, InlineMath } from './extensions.ts'
import { blockAt, stepPaths } from './blocks.ts'

export const selectedBlockKey = new PluginKey<number | null>('selectedBlock')

export function selectedBlock(state: EditorState): number | null {
  return selectedBlockKey.getState(state) ?? null
}

const SelectedBlock = Extension.create({
  name: 'selectedBlock',
  addProseMirrorPlugins() {
    return [
      new Plugin<number | null>({
        key: selectedBlockKey,
        state: {
          init: () => null,
          apply(tr, pos) {
            const set = tr.getMeta(selectedBlockKey) as number | null | undefined
            if (set !== undefined) return set
            if (pos === null || !tr.docChanged) return pos
            const mapped = tr.mapping.map(pos, 1)
            return blockAt(tr.doc, mapped) ? mapped : null
          },
        },
        props: {
          decorations(state) {
            const pos = selectedBlockKey.getState(state)
            const node = pos === null || pos === undefined ? null : state.doc.nodeAt(pos)
            if (pos === null || pos === undefined || !node) return null
            return DecorationSet.create(state.doc, [Decoration.node(pos, pos + node.nodeSize, { class: 'is-selected' })])
          },
        },
      }),
    ]
  },
})

export const flashKey = new PluginKey<number | null>('flash')

const Flash = Extension.create({
  name: 'flash',
  addProseMirrorPlugins() {
    return [
      new Plugin<number | null>({
        key: flashKey,
        state: {
          init: () => null,
          apply(tr, pos) {
            const set = tr.getMeta(flashKey) as number | null | undefined
            if (set !== undefined) return set
            return pos === null || !tr.docChanged ? pos : tr.mapping.map(pos, 1)
          },
        },
        props: {
          decorations(state) {
            const pos = flashKey.getState(state)
            const node = pos === null || pos === undefined ? null : state.doc.nodeAt(pos)
            if (pos === null || pos === undefined || !node) return null
            return DecorationSet.create(state.doc, [Decoration.node(pos, pos + node.nodeSize, { class: 'flash' })])
          },
        },
      }),
    ]
  },
})

const StepNumbers = Extension.create({
  name: 'stepNumbers',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        props: {
          decorations(state) {
            const marks = stepPaths(state.doc).map(({ pos, n, first, last }) => {
              const node = state.doc.nodeAt(pos)!
              const cls = [first && 'run-first', last && 'run-last'].filter(Boolean).join(' ')
              return Decoration.node(pos, pos + node.nodeSize, { 'data-n': String(n), class: cls })
            })
            return DecorationSet.create(state.doc, marks)
          },
        },
      }),
    ]
  },
})

export type OpenMath = (pos: number, anchor: HTMLElement) => void

function drawMath(el: HTMLElement, latex: string, displayMode: boolean) {
  if (!latex) {
    el.textContent = 'Empty formula'
    el.classList.add('math-empty')
    return
  }
  el.classList.remove('math-empty')
  katex.render(latex, el, { displayMode, throwOnError: false })
}

function mathView(displayMode: boolean, open: OpenMath) {
  return ({ node, getPos }: { node: import('@tiptap/pm/model').Node; getPos: () => number | undefined }) => {
    const dom = document.createElement(displayMode ? 'div' : 'span')
    dom.className = displayMode ? 'math-block' : 'm'
    dom.setAttribute('role', 'button')
    dom.setAttribute('aria-label', 'Formula, edit')
    drawMath(dom, node.attrs.latex as string, displayMode)
    dom.addEventListener('click', () => {
      const pos = getPos()
      if (pos !== undefined) open(pos, dom)
    })
    return {
      dom,
      update(next: import('@tiptap/pm/model').Node) {
        if (next.type !== node.type) return false
        node = next
        drawMath(dom, next.attrs.latex as string, displayMode)
        return true
      },
      ignoreMutation: () => true,
    }
  }
}

export function mathWithViews(open: OpenMath) {
  return [
    InlineMath.extend({ addNodeView: () => mathView(false, open) }),
    BlockMath.extend({ addNodeView: () => mathView(true, open) }),
  ]
}

export const notesPlaceholder = Placeholder.configure({
  includeChildren: true,
  showOnlyCurrent: false,
  placeholder: ({ node, editor, pos, hasAnchor }) => {
    if (node.type.name === 'blockTitle') {
      const owner = editor.state.doc.resolve(pos).parent.type.name
      return owner === 'step' ? 'What happens in this step' : 'Add a title (optional)'
    }
    if (node.type.name === 'heading') return 'Heading'
    return hasAnchor ? 'Write something' : ''
  },
})

export const editorPlugins = [SelectedBlock, Flash, StepNumbers, notesPlaceholder]
