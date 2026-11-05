import assert from 'node:assert/strict'
import { test } from 'node:test'
import { titleMarkdown } from '../src/notes/editor/extensions.ts'
import { toDoc, toMarkdown, notesSchema } from '../src/notes/editor/markdown.ts'

/** Markdown → editor document (checked against the schema) → Markdown. */
function roundTrip(markdown: string): string {
  const doc = toDoc(markdown)
  notesSchema.nodeFromJSON(doc).check()
  return toMarkdown(doc)
}

const EVERY_CONSTRUCT = `## Apparent weight

The rider's **apparent weight** is $P$, *not* $W$. A ticket costs $5 and $10 later. ~~old~~ [a link](https://example.com) and \`code\`.

:::box[Problem]{color=indigo}
Explain how the **apparent weight** changes around a loop with radius $r$.
:::

:::step[Identify the forces]{color=indigo}
At the bottom, $P$ points up.

:::box[Key idea]{color=blue}
The apparent weight is the reaction force $P$.
:::

::figure{#v000001}
:::

:::step[Write the resultant force]{color=indigo}
$$
F = P - W \\quad\\Rightarrow\\quad P = F + W
$$
:::

:::box{color=grey}
No title here.
:::

:::box[Action items]{color=grey}
- [ ] Sam fixes the upload
- [x] Priya checks contrast
:::

> A quote

1. one
2. two

- a
- b

---

\`\`\`python
:::box
x = 1
\`\`\`

### Small heading

Last paragraph.`

test('every construct of the notes format comes back as it was written', () => {
  assert.equal(roundTrip(EVERY_CONSTRUCT), EVERY_CONSTRUCT)
})

test('a table comes back the same table (columns are padded)', () => {
  const out = roundTrip('| Issue | Owner |\n| --- | --- |\n| Upload | Sam |')
  assert.equal(out, '| Issue  | Owner |\n| ------ | ----- |\n| Upload | Sam   |')
  assert.equal(roundTrip(out), out)
})

test('a second pass changes nothing', () => {
  const once = roundTrip(EVERY_CONSTRUCT + '\n\nA * b and x_1 and 2 < 3.')
  assert.equal(roundTrip(once), once)
})

test('titles keep maths, marks and brackets', () => {
  for (const md of [
    ':::box[Definition: $v^2/r$ and **bold**]{color=blue}\nBody\n:::',
    ':::box[Title with \\] bracket]{color=red}\nBody\n:::',
    ':::step[Étape ✓ 収益]{color=teal}\nTexte.\n:::',
  ]) {
    assert.equal(roundTrip(md), md)
  }
})

test('every colour of the palette is kept; an unknown one becomes the default', () => {
  for (const c of ['grey', 'indigo', 'blue', 'teal', 'green', 'amber', 'orange', 'red', 'pink', 'violet']) {
    assert.equal(roundTrip(`:::box[A]{color=${c}}\nx\n:::`), `:::box[A]{color=${c}}\nx\n:::`)
  }
  assert.equal(roundTrip(':::box[A]{color=purple}\nx\n:::'), ':::box[A]{color=grey}\nx\n:::')
  assert.equal(roundTrip(':::step[A]\nx\n:::'), ':::step[A]{color=indigo}\nx\n:::')
})

test('a block with no end is closed; an empty block keeps a paragraph to type in', () => {
  assert.equal(roundTrip(':::box[Open]{color=green}\nRuns to the end'), ':::box[Open]{color=green}\nRuns to the end\n:::')
  const empty = toDoc(':::step[Empty]{color=indigo}\n:::')
  notesSchema.nodeFromJSON(empty).check()
  assert.equal(empty.content?.[0].content?.[1].type, 'paragraph')
  assert.equal(toMarkdown(empty), ':::step[Empty]{color=indigo}\n:::')
})

test('prices are text, maths is maths', () => {
  const doc = toDoc('Costs $5 and $10, while $x^2$ is maths.')
  const inline = doc.content?.[0].content ?? []
  assert.deepEqual(inline.filter((n) => n.type === 'inlineMath').map((n) => n.attrs?.latex), ['x^2'])
})

test('empty notes are one empty paragraph and save as nothing', () => {
  const doc = toDoc('')
  notesSchema.nodeFromJSON(doc).check()
  assert.equal(toMarkdown(doc), '')
})

test('raw HTML is kept as text, never as HTML', () => {
  const out = roundTrip('Before <b>raw</b> after')
  assert.ok(!out.includes('<b>'))
  assert.ok(out.includes('raw'))
})

test('an image from outside the session becomes its text, in a valid document', () => {
  for (const md of ['![outside](https://example.com/x.png)', ':::box[A]\n![inside a box](https://e.com/y.png)\n:::', 'Before\n\n![a](https://e.com/a.png)\n\nAfter']) {
    const doc = toDoc(md)
    notesSchema.nodeFromJSON(doc).check()
    assert.ok(!toMarkdown(doc).includes('!['))
  }
})

test('a block of a kind Iris does not know is edited as a box, as the reading view draws it', () => {
  assert.equal(roundTrip(':::warning[Careful]{color=red}\nText.\n:::'), ':::box[Careful]{color=red}\nText.\n:::')
})

test('code is saved exactly as written: blank lines and &nbsp; inside it are kept', () => {
  const md = 'Before.\n\n```python\ndef a():\n    pass\n\n\ndef b():\n    return "&nbsp;"\n&nbsp;\n```\n\nAfter.'
  assert.equal(roundTrip(md), md)
  const boxed = ':::box[Code]{color=grey}\n```\nx\n\n\ny\n```\n:::'
  assert.equal(roundTrip(boxed), boxed)
})

test('titles keep balanced brackets, inside maths too', () => {
  const title = (md: string) => toDoc(md).content?.[0].content?.[0]
  for (const md of [
    ':::box[Closed interval [a, b]]{color=blue}\nx\n:::',
    ':::box[Key idea: arr[i] is O(1)]{color=blue}\nx\n:::',
    ':::step[Interval $[a,b]$]{color=indigo}\nx\n:::',
  ]) {
    const once = roundTrip(md)
    assert.deepEqual(title(once), title(md), once) // the same title, read back
    assert.equal(roundTrip(once), once)
  }
  // Maths in a title keeps its LaTeX exactly.
  assert.equal(roundTrip(':::step[Interval $[a,b]$]{color=indigo}\nx\n:::'), ':::step[Interval $[a,b]$]{color=indigo}\nx\n:::')
})

test('only unpaired brackets in a title are escaped (as the backend does)', () => {
  assert.equal(titleMarkdown('Closed interval [a, b]'), 'Closed interval [a, b]')
  assert.equal(titleMarkdown('Half-open [a, b)'), 'Half-open \\[a, b)')
  assert.equal(titleMarkdown('Ends with ]'), 'Ends with \\]')
  assert.equal(titleMarkdown('Already \\] escaped'), 'Already \\] escaped')
  assert.equal(titleMarkdown('Ends with \\'), 'Ends with \\\\')
})
