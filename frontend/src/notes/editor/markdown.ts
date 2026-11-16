import { getSchema, type JSONContent } from '@tiptap/core'
import { MarkdownManager } from '@tiptap/markdown'
import { notesExtensions } from './extensions.ts'

const extensions = notesExtensions()
const manager = new MarkdownManager({ extensions })
export const notesSchema = getSchema(extensions)

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

export function toDoc(markdown: string): JSONContent {
  const doc = wrapInline(manager.parse(markdown))
  return doc.content?.length ? doc : { type: 'doc', content: [{ type: 'paragraph' }] }
}

const FENCE = /^\s*(`{3,}|~{3,})/

export function toMarkdown(doc: JSONContent): string {
  const out: string[] = []
  let fence: string | null = null
  for (const line of manager.serialize(doc).split('\n')) {
    const f = line.match(FENCE)
    if (fence !== null) {
      if (f && f[1][0] === fence[0] && f[1].length >= fence.length && !line.trim().slice(f[1].length)) fence = null
      out.push(line)
      continue
    }
    if (f) fence = f[1]
    const text = line.trim() === '&nbsp;' ? '' : line
    if (!text.trim() && !out[out.length - 1]?.trim()) continue
    out.push(text)
  }
  return out.join('\n').trim()
}
