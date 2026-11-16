import { Node, type JSONContent, type MarkdownToken } from '@tiptap/core'
import { TaskItem, TaskList } from '@tiptap/extension-list'
import { TableKit } from '@tiptap/extension-table'
import StarterKit from '@tiptap/starter-kit'
import { blockEnd, colourOf, FIGURE, isColour, OPEN, type Colour } from '../format.ts'

export type Container = 'box' | 'step'
export const DEFAULT_COLOUR: Record<Container, Colour> = { box: 'grey', step: 'indigo' }

interface ContainerToken extends MarkdownToken {
  color: Colour
  titleTokens: MarkdownToken[]
  tokens: MarkdownToken[]
}

export function titleMarkdown(rendered: string): string {
  const out: string[] = []
  const opened: number[] = []
  const text = rendered.replace(/\n+/g, ' ')
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (c === '\\') {
      out.push(i + 1 < text.length ? text.slice(i, i + 2) : '\\\\')
      i++
    } else if (c === '[') {
      opened.push(out.length)
      out.push(c)
    } else if (c === ']') {
      out.push(opened.pop() === undefined ? '\\]' : c)
    } else out.push(c)
  }
  for (const at of opened) out[at] = '\\['
  return out.join('')
}

function container(name: Container) {
  return Node.create({
    name,
    group: 'block',
    content: 'blockTitle block+',
    defining: true,
    isolating: true,
    addAttributes() {
      return { color: { default: DEFAULT_COLOUR[name], rendered: false } }
    },
    parseHTML() {
      return name === 'box'
        ? [{ tag: 'section[data-type="box"]' }]
        : [{ tag: 'div[data-type="step"]', contentElement: 'section' }]
    },
    renderHTML({ node }) {
      const colour = `c-${node.attrs.color as string}`
      return name === 'box'
        ? ['section', { 'data-type': 'box', class: `blk ${colour}` }, 0]
        : ['div', { 'data-type': 'step', class: `run-item ${colour}` }, ['section', { class: 'blk blk-step' }, 0]]
    },
    markdownTokenizer: {
      name,
      level: 'block',
      start: (src: string) => src.match(name === 'box' ? /^:{3,}(?!step\b)[A-Za-z]/m : /^:{3,}step\b/m)?.index ?? -1,
      tokenize(src, _tokens, lexer) {
        const lines = src.split('\n')
        const m = lines[0].match(OPEN)
        if (!m || (name === 'step') !== (m[2] === 'step')) return undefined
        const end = blockEnd(lines)
        const last = end === -1 ? lines.length : end
        const written = colourOf(m[4])
        return {
          type: name,
          raw: lines.slice(0, Math.min(last + 1, lines.length)).join('\n') + (last + 1 < lines.length ? '\n' : ''),
          color: isColour(written) ? written : DEFAULT_COLOUR[name],
          titleTokens: lexer.inlineTokens(m[3] ?? ''),
          tokens: lexer.blockTokens(lines.slice(1, last).join('\n')),
        } as ContainerToken
      },
    },
    parseMarkdown(token, h) {
      const t = token as ContainerToken
      const body = h.parseChildren(t.tokens)
      return h.createNode(name, { color: t.color }, [
        h.createNode('blockTitle', {}, h.parseInline(t.titleTokens)),
        ...(body.length ? body : [h.createNode('paragraph', {}, [])]),
      ])
    },
    renderMarkdown(node, h) {
      const [title, ...body] = node.content ?? []
      const text = title?.content?.length ? titleMarkdown(h.renderChildren(title.content)) : ''
      const inner = h.renderChildren(body, '\n\n').trim()
      return `:::${name}${text ? `[${text}]` : ''}{color=${node.attrs?.color ?? DEFAULT_COLOUR[name]}}\n${inner ? `${inner}\n` : ''}:::`
    },
  })
}

export const Box = container('box')
export const Step = container('step')

export const BlockTitle = Node.create({
  name: 'blockTitle',
  content: 'inline*',
  defining: true,
  parseHTML: () => [{ tag: 'p.blk-head' }],
  renderHTML: () => ['p', { class: 'blk-head' }, 0],
})

export const Figure = Node.create({
  name: 'figure',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes: () => ({ id: { default: '' } }),
  parseHTML: () => [{ tag: 'figure[data-figure]', getAttrs: (el) => ({ id: el.getAttribute('data-figure') }) }],
  renderHTML: ({ node }) => ['figure', { class: 'figure', 'data-figure': node.attrs.id }],
  markdownTokenizer: {
    name: 'figure',
    level: 'block',
    start: (src: string) => src.match(/^::figure\{/m)?.index ?? -1,
    tokenize(src) {
      const line = src.split('\n', 1)[0]
      const m = line.match(FIGURE)
      return m ? { type: 'figure', raw: src.length > line.length ? `${line}\n` : line, id: m[1] } : undefined
    },
  },
  parseMarkdown: (token, h) => h.createNode('figure', { id: (token as MarkdownToken & { id: string }).id }),
  renderMarkdown: (node) => `::figure{#${node.attrs?.id as string}}`,
})

const INLINE_MATH = /^\$(?=\S)([^$\n]*?[^\s\\])\$(?!\d)/

export const InlineMath = Node.create({
  name: 'inlineMath',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes: () => ({ latex: { default: '' } }),
  parseHTML: () => [{ tag: 'span[data-latex]', getAttrs: (el) => ({ latex: el.getAttribute('data-latex') }) }],
  renderHTML: ({ node }) => ['span', { class: 'm', 'data-latex': node.attrs.latex }],
  markdownTokenizer: {
    name: 'inlineMath',
    level: 'inline',
    start: (src: string) => src.indexOf('$'),
    tokenize(src) {
      if (src.startsWith('$$')) return undefined
      const m = src.match(INLINE_MATH)
      return m ? { type: 'inlineMath', raw: m[0], latex: m[1] } : undefined
    },
  },
  parseMarkdown: (token, h) => h.createNode('inlineMath', { latex: (token as MarkdownToken & { latex: string }).latex }),
  renderMarkdown: (node) => `$${node.attrs?.latex as string}$`,
})

export const BlockMath = Node.create({
  name: 'blockMath',
  group: 'block',
  atom: true,
  selectable: true,
  addAttributes: () => ({ latex: { default: '' } }),
  parseHTML: () => [{ tag: 'div[data-latex]', getAttrs: (el) => ({ latex: el.getAttribute('data-latex') }) }],
  renderHTML: ({ node }) => ['div', { class: 'math-block', 'data-latex': node.attrs.latex }],
  markdownTokenizer: {
    name: 'blockMath',
    level: 'block',
    start: (src: string) => src.match(/^\$\$/m)?.index ?? -1,
    tokenize(src) {
      const m = src.match(/^\$\$([\s\S]+?)\$\$[ \t]*(?:\n|$)/)
      return m ? { type: 'blockMath', raw: m[0], latex: m[1].trim() } : undefined
    },
  },
  parseMarkdown: (token, h) => h.createNode('blockMath', { latex: (token as MarkdownToken & { latex: string }).latex }),
  renderMarkdown: (node) => (node.attrs?.latex ? `$$\n${node.attrs.latex as string}\n$$` : ''),
})

export function notesExtensions() {
  return [
    StarterKit.configure({
      underline: false,
      trailingNode: false,
      link: { openOnClick: false },
    }),
    TableKit,
    TaskList,
    TaskItem.configure({ nested: true }),
    Box,
    Step,
    BlockTitle,
    Figure,
    InlineMath,
    BlockMath,
  ]
}

export type { JSONContent }
