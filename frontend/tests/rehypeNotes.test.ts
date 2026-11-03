import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { Element, Root } from 'hast'
import remarkMath from 'remark-math'
import remarkParse from 'remark-parse'
import remarkRehype from 'remark-rehype'
import { unified } from 'unified'
import { rehypeNotes } from '../src/notes/rehypeNotes.ts'

function render(markdown: string): Element[] {
  const processor = unified().use(remarkParse).use(remarkMath).use(remarkRehype).use(rehypeNotes)
  const tree = processor.runSync(processor.parse(markdown)) as Root
  return tree.children.filter((n): n is Element => n.type === 'element')
}

const classes = (el: Element) => (el.properties.className as string[] | undefined) ?? []

test('steps become cards holding their working, until the next heading', () => {
  const out = render(
    [
      '**Problem:** Express $3x^2 + 18x - 1$ in the form $a(x+b)^2 + c$.',
      '**Step 1: Factor out 3.**',
      '$$\n3(x^2 + 6x) - 1\n$$',
      'Completing the square needs $x^2$ on its own.',
      '**Step 2: Halve and square.**',
      '**Answer:** $3(x+3)^2 - 28$',
      '## Next topic',
      'Plain paragraph.',
    ].join('\n\n'),
  )
  assert.deepEqual(
    out.map((el) => [el.tagName, classes(el).join(' ')]),
    [
      ['section', 'card card-problem'],
      ['section', 'card card-step'],
      ['section', 'card card-step'],
      ['section', 'card card-answer'],
      ['h2', ''],
      ['p', ''],
    ],
  )
  const step1 = out[1].children.filter((n): n is Element => n.type === 'element')
  assert.equal(step1.length, 3) // title, display math, explanation
})

test('figures and callouts', () => {
  const [figure, key, warn] = render(
    [
      '![Orbit](visuals/v1.svg)\n*Two black holes orbiting*',
      '> **Key idea:** halve, then square.',
      '> **Watch out:** multiply the $-9$ by 3.',
    ].join('\n\n'),
  )
  assert.equal(figure.tagName, 'figure')
  const [img, caption] = figure.children as Element[]
  assert.equal(img.tagName, 'img')
  assert.equal(caption.tagName, 'figcaption')
  assert.deepEqual(classes(key), ['callout-key'])
  assert.deepEqual(classes(warn), ['callout-warn'])
})
