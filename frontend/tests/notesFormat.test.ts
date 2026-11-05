import assert from 'node:assert/strict'
import { test } from 'node:test'
import { figureIds, parseNotes, withPaths, type BlockNode, type NotesNode } from '../src/notes/format.ts'

const block = (n: NotesNode | undefined) => n as BlockNode

test('each ::: closes the most recent block still open', () => {
  const nodes = parseNotes(
    [
      'Intro.',
      ':::step[Find the forces]{color=indigo}',
      'At the bottom.',
      ':::box[Key idea]{color=blue}',
      'The reaction force.',
      ':::',
      '::figure{#v1}',
      ':::',
      'After.',
    ].join('\n'),
  )
  assert.deepEqual(nodes.map((n) => n.kind), ['markdown', 'block', 'markdown'])
  const step = block(nodes[1])
  assert.equal(step.command, 'step')
  assert.equal(step.title, 'Find the forces')
  assert.equal(step.colour, 'indigo')
  assert.equal(step.closed, true)
  assert.deepEqual(step.children.map((n) => n.kind), ['markdown', 'block', 'figure'])
  assert.equal(block(step.children[1]).title, 'Key idea')
  assert.deepEqual(figureIds(nodes), ['v1'])
})

test('no title, no colour, unknown colour and unknown command', () => {
  const [plain, purple, odd] = parseNotes(':::box\nx\n:::\n:::box[A]{color=purple}\ny\n:::\n:::warning[W]\nz\n:::')
  assert.equal(block(plain).title, null)
  assert.equal(block(plain).colour, 'grey')
  assert.equal(block(plain).badColour, null)
  assert.equal(block(purple).colour, 'grey')
  assert.equal(block(purple).badColour, 'purple')
  assert.equal(block(odd).command, 'warning')
})

test('an unclosed block runs to the end; a stray ::: is dropped', () => {
  const nodes = parseNotes(':::\nBefore.\n:::box[Open]\nRuns on.\n\nAnd on.')
  assert.equal(nodes.length, 2)
  assert.equal(nodes[0].kind, 'markdown')
  assert.equal((nodes[0] as { text: string }).text, 'Before.')
  assert.equal(block(nodes[1]).closed, false)
  assert.equal(block(nodes[1]).children.length, 1)
})

test('a third level is marked too deep but its content is kept', () => {
  const [outer] = parseNotes(':::box[A]\n:::box[B]\n:::box[C]\ndeep\n:::\n:::\n:::')
  const inner = block(block(outer).children[0])
  const deepest = block(inner.children[0])
  assert.equal(block(outer).tooDeep, false)
  assert.equal(inner.tooDeep, false)
  assert.equal(deepest.tooDeep, true)
  assert.deepEqual(deepest.children, [{ kind: 'markdown', text: 'deep' }])
})

test('lines inside fenced code are text', () => {
  const nodes = parseNotes('```md\n:::box[Not a box]\n::figure{#x}\n:::\n```\n\n~~~\n:::\n~~~')
  assert.equal(nodes.length, 1)
  assert.equal(nodes[0].kind, 'markdown')
})

test('titles keep inline Markdown and escaped brackets; non-ASCII is fine', () => {
  const [a, b] = parseNotes(':::box[Definition: $v^2/r$ \\] **x**]{color=blue}\n:::\n:::step[Étape ✓ 収益]{color=teal}\n:::')
  assert.equal(block(a).title, 'Definition: $v^2/r$ \\] **x**')
  assert.equal(block(b).title, 'Étape ✓ 収益')
  assert.equal(block(b).colour, 'teal')
})

test('steps in a row form one path; anything between them starts a new one', () => {
  const nodes = parseNotes(
    [':::step[A]', ':::', ':::step[B]', ':::', 'Between.', ':::step[C]', ':::', ':::box[Not a step]', ':::', ':::step[D]', ':::'].join('\n'),
  )
  const grouped = withPaths(nodes)
  assert.deepEqual(
    grouped.map((g) => (g.kind === 'path' ? g.steps.map((s) => s.title) : g.kind)),
    [['A', 'B'], 'markdown', ['C'], 'block', ['D']],
  )
})

test('steps inside a step or box form their own path; too-deep steps are not on one', () => {
  const [outer] = parseNotes(':::box[Method]\n:::step[One]\n:::\n:::step[Two]\n:::\n:::')
  const inner = withPaths(block(outer).children)
  assert.equal(inner.length, 1)
  assert.equal(inner[0].kind, 'path')
})

test('empty notes', () => {
  assert.deepEqual(parseNotes(''), [])
})

test('titles may hold brackets in pairs', () => {
  const titles = parseNotes(':::box[Closed interval [a, b]]{color=blue}\n:::\n:::box[$x[i]$ and \\]]\n:::').map((n) => block(n).title)
  assert.deepEqual(titles, ['Closed interval [a, b]', '$x[i]$ and \\]'])
})
