import assert from 'node:assert/strict'
import { test } from 'node:test'
import { EditorState, TextSelection, type Transaction } from '@tiptap/pm/state'
import {
  addBelow, blockAt, blockOfSelection, canLand, makeMaths, moveBy, moveTo, remove, setColour,
  stepPaths, turnInto, turnIntoChoices, usedFigures,
} from '../src/notes/editor/blocks.ts'
import { notesSchema, toDoc, toMarkdown } from '../src/notes/editor/markdown.ts'

const state = (md: string) => EditorState.create({ doc: notesSchema.nodeFromJSON(toDoc(md)) })
const md = (s: EditorState) => toMarkdown(s.doc.toJSON())
const apply = (s: EditorState, tr: Transaction | null) => {
  assert.ok(tr, 'expected a change')
  const next = s.apply(tr)
  next.doc.check()
  return next
}
const top = (s: EditorState, i: number) => {
  let pos = 0
  for (let k = 0; k < i; k++) pos += s.doc.child(k).nodeSize
  return pos
}
const inside = (s: EditorState, parent: number, i: number) => {
  const node = s.doc.nodeAt(parent)!
  let pos = parent + 1
  for (let k = 0; k < i; k++) pos += node.child(k).nodeSize
  return pos
}

const NOTES = [
  'Intro.',
  ':::step[One]{color=indigo}',
  'First.',
  ':::box[Key idea]{color=blue}',
  'Inside.',
  ':::',
  ':::',
  ':::step[Two]{color=indigo}',
  'Second.',
  ':::',
  '## Heading',
].join('\n')

test('blocks know their depth and the box or step around them', () => {
  const s = state(NOTES)
  const step = top(s, 1)
  const key = inside(s, step, 2)
  assert.deepEqual([blockAt(s.doc, step)?.depth, blockAt(s.doc, step)?.parent], [0, null])
  assert.deepEqual([blockAt(s.doc, key)?.depth, blockAt(s.doc, key)?.parent], [1, step])
  assert.equal(blockAt(s.doc, step + 1), null)
})

test('the selection picks the innermost block; a title picks its box or step', () => {
  const s = state(NOTES)
  const step = top(s, 1)
  const key = inside(s, step, 2)
  const at = (pos: number) => blockOfSelection(TextSelection.create(s.doc, pos))
  assert.equal(at(step + 3), step)
  assert.equal(at(inside(s, key, 1) + 2), inside(s, key, 1))
  assert.equal(at(1), 0)
})

test('turn into: text ↔ heading, wrap in a box or step, unwrap, box ↔ step', () => {
  let s = state('Plain text.\n\n## A heading')
  s = apply(s, turnInto(s, top(s, 0), 'heading'))
  assert.equal(md(s), '## Plain text.\n\n## A heading')
  s = apply(s, turnInto(s, top(s, 1), 'box'))
  assert.equal(md(s), '## Plain text.\n\n:::box[A heading]{color=grey}\n:::')
  s = apply(s, turnInto(s, top(s, 0), 'step'))
  assert.equal(md(s), ':::step[Plain text.]{color=indigo}\n:::\n\n:::box[A heading]{color=grey}\n:::')
  s = apply(s, turnInto(s, top(s, 1), 'step'))
  assert.ok(md(s).endsWith(':::step[A heading]{color=grey}\n:::'))
  s = apply(s, turnInto(s, top(s, 0), 'text'))
  assert.ok(md(s).startsWith('Plain text.\n\n:::step[A heading]'))
  s = state(':::box[Title]{color=blue}\nBody.\n:::')
  assert.equal(md(apply(s, turnInto(s, 0, 'text'))), 'Title\n\nBody.')
})

test('turn into never makes a step inside a block or a third level', () => {
  const s = state(':::box[A]\n:::box[B]\nDeep text.\n:::\nText.\n:::')
  const a = top(s, 0)
  const b = inside(s, a, 1)
  const text = inside(s, a, 2)
  const deep = inside(s, b, 1)
  assert.deepEqual(turnIntoChoices(blockAt(s.doc, text)!), ['heading', 'box'])
  assert.deepEqual(turnIntoChoices(blockAt(s.doc, deep)!), ['heading'])
  assert.deepEqual(turnIntoChoices(blockAt(s.doc, b)!), ['text', 'heading'])
  assert.equal(turnInto(s, deep, 'box'), null)
})

test('lists, tables, maths and figures can be wrapped but not turned into text', () => {
  const s = state('- a\n- b\n\n::figure{#v1}')
  assert.deepEqual(turnIntoChoices(blockAt(s.doc, top(s, 0))!), ['box', 'step'])
  const wrapped = apply(s, turnInto(s, top(s, 1), 'box'))
  assert.equal(md(wrapped), '- a\n- b\n\n:::box{color=grey}\n::figure{#v1}\n:::')
})

test('colour is set on boxes and steps only', () => {
  let s = state(':::box[A]{color=grey}\nx\n:::\n\nText.')
  s = apply(s, setColour(s, 0, 'violet'))
  assert.ok(md(s).startsWith(':::box[A]{color=violet}'))
  assert.equal(setColour(s, top(s, 1), 'red'), null)
})

test('move up and down swap neighbours and step out at the edge of a box or step', () => {
  let s = state(NOTES)
  const step = top(s, 1)
  const key = inside(s, step, 2)
  let moved = moveBy(s, key, 1)!
  s = apply(s, moved.tr)
  assert.equal(md(s), [
    'Intro.', '', ':::step[One]{color=indigo}', 'First.', ':::', '', ':::box[Key idea]{color=blue}', 'Inside.', ':::',
    '', ':::step[Two]{color=indigo}', 'Second.', ':::', '', '## Heading',
  ].join('\n'))
  assert.equal(s.doc.nodeAt(moved.pos)?.type.name, 'box')
  moved = moveBy(s, moved.pos, -1)!
  s = apply(s, moved.tr)
  assert.ok(md(s).startsWith('Intro.\n\n:::box[Key idea]'))
  assert.equal(moveBy(s, 0, -1), null)
  const stepPos = top(s, 2)
  moved = moveBy(s, inside(s, stepPos, 1), -1)!
  s = apply(s, moved.tr)
  assert.ok(md(s).includes('Inside.\n:::\n\nFirst.\n\n:::step[One]{color=indigo}\n:::'))
})

test('a move is one transaction, so one undo restores it', () => {
  const s = state(NOTES)
  const moved = moveTo(s, top(s, 0), { parent: top(s, 2), index: 2 })!
  assert.equal(moved.tr.steps.length, 2)
  const after = apply(s, moved.tr)
  assert.ok(md(after).includes(':::step[Two]{color=indigo}\nSecond.\n\nIntro.\n:::'))
})

test('landing rules: no step inside a block, no third level, never inside itself', () => {
  const s = state(NOTES)
  const one = top(s, 1)
  const two = top(s, 2)
  const key = inside(s, one, 2)
  assert.equal(canLand(s.doc, two, { parent: one, index: 1 }), false)
  assert.equal(canLand(s.doc, key, { parent: two, index: 2 }), true)
  assert.equal(canLand(s.doc, one, { parent: null, index: 4 }), true)
  const nested = state(':::box[A]\n:::box[B]\nx\n:::\n:::\n\n:::box[C]\ny\n:::')
  assert.equal(canLand(nested.doc, 0, { parent: top(nested, 1), index: 1 }), false)
  assert.equal(canLand(nested.doc, top(nested, 1), { parent: inside(nested, 0, 1), index: 1 }), false)
  assert.equal(moveTo(s, top(s, 0), { parent: null, index: 0 }), null)
})

test('moving the last block out of a box leaves an empty paragraph to type in', () => {
  let s = state(':::box[A]\nOnly.\n:::\n\nAfter.')
  const moved = moveTo(s, inside(s, 0, 1), { parent: null, index: 2 })!
  s = apply(s, moved.tr)
  assert.equal(md(s), ':::box[A]{color=grey}\n:::\n\nAfter.\n\nOnly.')
})

test('add below: in place, a step outside any block, a box no deeper than allowed', () => {
  let s = state(NOTES)
  const one = top(s, 1)
  const first = inside(s, one, 1)
  let added = addBelow(s, first, 'box')!
  s = apply(s, added.tr)
  assert.equal(blockAt(s.doc, added.pos)?.parent, one)
  added = addBelow(s, inside(s, top(s, 1), 1), 'step')!
  s = apply(s, added.tr)
  assert.equal(blockAt(s.doc, added.pos)?.parent, null)
  assert.equal(s.doc.nodeAt(added.pos)?.type.name, 'step')
  const deep = state(':::box[A]\n:::box[B]\nx\n:::\n:::')
  added = addBelow(deep, inside(deep, inside(deep, 0, 1), 1), 'box')!
  const after = apply(deep, added.tr)
  assert.equal(blockAt(after.doc, added.pos)?.depth, 1)
  const fig = addBelow(s, 0, { figure: 'v3' })!
  assert.ok(usedFigures(apply(s, fig.tr).doc).has('v3'))
})

test('empty maths added below are dropped when saved', () => {
  const s = state('Text.')
  const added = addBelow(s, 0, 'maths')!
  assert.equal(md(apply(s, added.tr)), 'Text.')
})

test('remove a block; the last one leaves an empty document', () => {
  let s = state('Only.')
  s = apply(s, remove(s, 0))
  assert.equal(s.doc.childCount, 1)
  assert.equal(md(s), '')
})

test('make maths turns the selected text into an inline formula', () => {
  const s = state('Area is pi r^2 here.')
  const from = 1 + 'Area is '.length
  const to = from + 'pi r^2'.length
  const sel = s.apply(s.tr.setSelection(TextSelection.create(s.doc, from, to)))
  assert.equal(md(apply(sel, makeMaths(sel))), 'Area is $pi r^2$ here.')
  assert.equal(makeMaths(s), null)
})

test('steps in a row are numbered as one path; anything between restarts it', () => {
  const s = state(':::step[A]\n:::\n:::step[B]\n:::\nBetween.\n:::step[C]\n:::\n:::box[M]\n:::step[D]\n:::\n:::')
  assert.deepEqual(
    stepPaths(s.doc).map(({ n, first, last }) => [n, first, last]),
    [[1, true, false], [2, false, true], [1, true, true], [1, true, true]],
  )
})
