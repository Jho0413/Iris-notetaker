// Notes Markdown in and out of the editor: the one place the editor's document meets the format.
import { getSchema, type JSONContent } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'
import { notesExtensions } from './extensions.ts'

const extensions = notesExtensions()
const manager = new MarkdownManager({ extensions })
export const notesSchema = getSchema(extensions)

/** Inline content left where blocks belong (what Markdown the editor has no node for, such as
 *  an image from outside the session, becomes its text) is put in a paragraph, as the schema
 *  needs. */
function wrapInline(node: JSONContent): JSONContent {
  const type = node.type ? notesSchema.nodes[node.type] : undefined
  if (!node.content || !type || type.inlineContent) return node
  const content: JSONContent[] = []
  for (const child of node.content.map(wrapInline)) {
    const inline = notesSchema.nodes[child.type ?? '']?.isInline
    const last = content[content.length - 1]
    if (!inline) content.push(child)
    else if (last?.type === 'paragraph' && last.attrs?.wrapped) last.content!.push(child)
    else content.push({ type: 'paragraph', attrs: { wrapped: true }, content: [child] })
  }
  for (const c of content) if (c.attrs?.wrapped) delete c.attrs
  return { ...node, content }
}

/** The editor document for notes Markdown. Never empty: a document needs one block. */
export function toDoc(markdown: string): JSONContent {
  const doc = wrapInline(manager.parse(markdown))
  return doc.content?.length ? doc : { type: 'doc', content: [{ type: 'paragraph' }] }
}

/** Notes Markdown for an editor document, in the notes format. */
export function toMarkdown(doc: JSONContent): string {
  // An empty paragraph is written as &nbsp; to keep it; in notes it is only a blank line.
  return manager
    .serialize(doc)
    .split('\n')
    .map((line) => (line.trim() === '&nbsp;' ? '' : line))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
