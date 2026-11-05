// The notes format, as the reading view draws it: Markdown with boxes, steps and figures.
//
//   :::box[Title]{color=green}    a box; no [ ] means no title, no colour means grey
//   …
//   :::
//   :::step[Title]{color=indigo}  a step; steps in a row form one numbered path
//   …
//   :::
//   ::figure{#v000001}            a figure from this session, by id
//
// The word after the colons is a command and is never shown. Each closing ::: closes the most
// recent block still open; blocks go one level deep; lines inside fenced code are text. The
// backend follows the same rules (backend/src/iris/notes_format.py).

export const PALETTE = ['grey', 'indigo', 'blue', 'teal', 'green', 'amber', 'orange', 'red', 'pink', 'violet'] as const
export type Colour = (typeof PALETTE)[number]
export const COMMANDS = ['box', 'step'] as const
const MAX_DEPTH = 2 // blocks open at once: a box inside a step or box, never deeper

// A title may hold brackets in pairs, one level deep ("[Interval [a, b]]", "[$x[i]$]"); an
// unpaired ] is written \].
export const OPEN = /^(:{3,})([A-Za-z][\w-]*)(?:\[((?:\\.|[^[\]\\]|\[(?:\\.|[^[\]\\])*\])*)\])?(?:\{([^}]*)\})?[ \t]*$/
export const CLOSE = /^:{3,}[ \t]*$/
export const FIGURE = /^::figure\{#([A-Za-z0-9_-]+)\}[ \t]*$/
const FENCE = /^ {0,3}(`{3,}|~{3,})/
const COLOUR = /(?:^|\s)color=("?)([^\s"}]*)\1(?=\s|$)/

/** What the reader is told when the notes can't be drawn as written. */
export const FORMAT_MESSAGES = {
  unclosed: "This block has no end, so it runs to the end of the notes.",
  unknownColour: (c: string) => `"${c}" isn't one of Iris's colours, so this is grey.`,
  unknownCommand: (c: string) => `"${c}" isn't a kind of block Iris knows, so this is drawn as a box.`,
  tooDeep: "This block is inside a block inside a block. Blocks go one level deep, so it isn't drawn as a box.",
  unknownFigure: (id: string) => `There's no figure "${id}" in this session.`,
  outsideImage: "Images from outside the session aren't shown.",
} as const

export interface MarkdownNode {
  kind: 'markdown'
  text: string
}
export interface FigureNode {
  kind: 'figure'
  id: string
}
export interface BlockNode {
  kind: 'block'
  /** "box" or "step"; anything else is drawn as a box and reported. */
  command: string
  /** As written between [ ] (inline Markdown), or null without a title. */
  title: string | null
  colour: Colour
  /** The colour as written, when it isn't one of the palette's. */
  badColour: string | null
  closed: boolean
  /** True when the block is deeper than blocks may go: its content is drawn, its frame isn't. */
  tooDeep: boolean
  children: NotesNode[]
}
export type NotesNode = MarkdownNode | FigureNode | BlockNode

export function colourOf(attrs: string | undefined): string | null {
  return attrs?.match(COLOUR)?.[2] ?? null
}

export function isColour(name: string | null | undefined): name is Colour {
  return (PALETTE as readonly string[]).includes(name ?? '')
}

/** The notes as a tree of Markdown, blocks and figures, following the stack rule. */
export function parseNotes(markdown: string): NotesNode[] {
  const root: NotesNode[] = []
  const stack: BlockNode[] = []
  let text: string[] = []
  let fence: string | null = null
  const here = () => (stack.length ? stack[stack.length - 1].children : root)
  const flush = () => {
    if (text.some((l) => l.trim())) here().push({ kind: 'markdown', text: text.join('\n') })
    text = []
  }
  for (const line of markdown.split('\n')) {
    if (fence !== null) {
      const t = line.trim()
      if (t.startsWith(fence) && !t.replaceAll(fence[0], '')) fence = null
      text.push(line)
      continue
    }
    const f = line.match(FENCE)
    if (f) {
      fence = f[1]
      text.push(line)
      continue
    }
    let m: RegExpMatchArray | null
    if ((m = line.match(FIGURE))) {
      flush()
      here().push({ kind: 'figure', id: m[1] })
    } else if ((m = line.match(OPEN))) {
      flush()
      const written = colourOf(m[4])
      const block: BlockNode = {
        kind: 'block',
        command: m[2],
        title: m[3] ?? null,
        colour: isColour(written) ? written : 'grey',
        badColour: written !== null && !isColour(written) ? written : null,
        closed: false,
        tooDeep: stack.length >= MAX_DEPTH,
        children: [],
      }
      here().push(block)
      stack.push(block)
    } else if (CLOSE.test(line)) {
      flush()
      const block = stack.pop()
      if (block) block.closed = true
      // A ::: that closes nothing is dropped: it would only show as stray colons.
    } else {
      text.push(line)
    }
  }
  flush()
  return root
}

/** The line closing the block opened on `lines[0]` (stack rule, code skipped), or -1. */
export function blockEnd(lines: readonly string[]): number {
  let depth = 0
  let fence: string | null = null
  for (let i = 1; i < lines.length; i++) {
    const line = lines[i]
    if (fence !== null) {
      const t = line.trim()
      if (t.startsWith(fence) && !t.replaceAll(fence[0], '')) fence = null
      continue
    }
    const f = line.match(FENCE)
    if (f) fence = f[1]
    else if (OPEN.test(line)) depth++
    else if (CLOSE.test(line)) {
      if (depth === 0) return i
      depth--
    }
  }
  return -1
}

export const isStep = (n: NotesNode): n is BlockNode =>
  n.kind === 'block' && n.command === 'step' && !n.tooDeep

/** A run of steps drawn as one numbered path. */
export interface StepPath {
  kind: 'path'
  steps: BlockNode[]
}

/** Nodes with each run of steps in a row gathered into a path, the way list items in a row form
 *  one list. Anything between two steps ends the path; the next one starts again at 1. */
export function withPaths(nodes: readonly NotesNode[]): (NotesNode | StepPath)[] {
  const out: (NotesNode | StepPath)[] = []
  for (const node of nodes) {
    const last = out[out.length - 1]
    if (isStep(node) && last?.kind === 'path') last.steps.push(node)
    else out.push(isStep(node) ? { kind: 'path', steps: [node] } : node)
  }
  return out
}

/** Every figure id the notes place, in order. */
export function figureIds(nodes: readonly NotesNode[]): string[] {
  return nodes.flatMap((n) => (n.kind === 'figure' ? [n.id] : n.kind === 'block' ? figureIds(n.children) : []))
}
