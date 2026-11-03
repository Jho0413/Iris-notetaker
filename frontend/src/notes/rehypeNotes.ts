// Turns the conventions the notes writer follows into structure the UI can style:
//   **Problem:** / **Step n: ...** / **Answer:** paragraphs  -> <section> cards holding the
//                                                              paragraphs and math that follow
//   > **Key idea:** / > **Watch out:**                       -> callout blockquotes (a quote after
//                                                              an answer ends the answer card)
//   ![caption](src) *caption*                                 -> <figure> with <figcaption>
import type { Element, ElementContent, Root, RootContent } from 'hast'

type Card = 'problem' | 'step' | 'answer'

function text(node: RootContent | ElementContent): string {
  if (node.type === 'text') return node.value
  if (node.type === 'element') return node.children.map(text).join('')
  return ''
}

function isBlank(node: RootContent | ElementContent): boolean {
  return node.type === 'text' && !node.value.trim()
}

/** Text of a paragraph's leading <strong>, or '' when it does not start with one. */
function lead(el: Element): string {
  const first = el.children.find((c) => !isBlank(c))
  return first?.type === 'element' && first.tagName === 'strong' ? text(first).trim() : ''
}

function card(node: RootContent): Card | null {
  if (node.type !== 'element' || node.tagName !== 'p') return null
  const t = lead(node)
  if (/^problem\b/i.test(t)) return 'problem'
  if (/^step\s*\d+/i.test(t)) return 'step'
  if (/^(answer|result|summary)\b/i.test(t)) return 'answer'
  return null
}

function addClass(el: Element, name: string): void {
  const current = el.properties.className
  const list = Array.isArray(current) ? current.map(String) : current ? [String(current)] : []
  el.properties.className = [...list, name]
}

function asFigure(el: Element): Element | null {
  if (el.tagName !== 'p') return null
  const parts = el.children.filter((c) => !isBlank(c))
  const img = parts[0]
  if (img?.type !== 'element' || img.tagName !== 'img') return null
  const rest = parts.slice(1)
  if (rest.some((c) => c.type !== 'element' || c.tagName !== 'em')) return null
  const caption = rest.flatMap((c) => (c.type === 'element' ? c.children : []))
  const children: ElementContent[] = [img]
  if (caption.length) {
    children.push({ type: 'element', tagName: 'figcaption', properties: {}, children: caption })
  }
  return { type: 'element', tagName: 'figure', properties: { className: ['figure'] }, children }
}

function markCallout(el: Element): void {
  if (el.tagName !== 'blockquote') return
  const first = el.children.find((c) => c.type === 'element')
  const t = first?.type === 'element' ? lead(first) : ''
  if (/^key (idea|point)/i.test(t)) addClass(el, 'callout-key')
  else if (/^(watch out|common mistake|warning|note)\b/i.test(t)) addClass(el, 'callout-warn')
}

export function rehypeNotes() {
  return (tree: Root) => {
    const out: RootContent[] = []
    let group: Element | null = null
    let groupKind: Card | null = null
    for (const node of tree.children) {
      const kind = card(node)
      const isHeading = node.type === 'element' && /^h[1-6]$|^hr$/.test(node.tagName)
      if (kind) {
        group = {
          type: 'element',
          tagName: 'section',
          properties: { className: ['card', `card-${kind}`] },
          children: [],
        }
        groupKind = kind
        out.push(group)
      } else if (isHeading) {
        group = null
      } else if (groupKind === 'answer' && node.type === 'element' && node.tagName === 'blockquote') {
        // Takeaways after the answer are about the whole example, not part of the answer.
        group = null
      }
      let item: RootContent = node
      if (node.type === 'element') {
        item = asFigure(node) ?? node
        if (item.type === 'element') markCallout(item)
      }
      if (group && item.type !== 'doctype') group.children.push(item as ElementContent)
      else out.push(item)
    }
    tree.children = out
  }
}
