import katex from 'katex'
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { PALETTE, type Colour } from './format'

export function Popover(props: {
  anchor: HTMLElement
  label: string
  className?: string
  role?: 'menu' | 'dialog'
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const { anchor, onClose } = props

  useLayoutEffect(() => {
    const menu = ref.current
    if (!menu) return
    const r = anchor.getBoundingClientRect()
    const width = menu.offsetWidth
    setPlace({
      top: r.bottom + window.scrollY + 6,
      left: Math.max(8, Math.min(r.left + window.scrollX, window.scrollX + document.documentElement.clientWidth - width - 8)),
    })
    const first = menu.querySelector<HTMLElement>('[aria-checked="true"], button:not([disabled]), textarea')
    first?.focus({ preventScroll: true })
  }, [anchor])

  useEffect(() => {
    const close = (e: PointerEvent) => {
      if (!ref.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose()
    }
    document.addEventListener('pointerdown', close, true)
    return () => document.removeEventListener('pointerdown', close, true)
  }, [anchor, onClose])

  return createPortal(
    <div
      ref={ref}
      className={`menu ${props.className ?? ''}`}
      role={props.role ?? 'menu'}
      aria-label={props.label}
      style={place ? { top: place.top, left: place.left } : { visibility: 'hidden' }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault()
          e.stopPropagation()
          onClose()
          anchor.focus()
          return
        }
        if ((e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'ArrowLeft' || e.key === 'ArrowRight') && props.role !== 'dialog') {
          const items = [...(ref.current?.querySelectorAll<HTMLElement>('button:not([disabled])') ?? [])]
          const at = items.indexOf(document.activeElement as HTMLElement)
          const step = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? 1 : -1
          items[(at + step + items.length) % items.length]?.focus()
          e.preventDefault()
        }
      }}
    >
      {props.children}
    </div>,
    document.body,
  )
}

export function MenuItem(props: {
  icon?: ReactNode
  note?: string
  danger?: boolean
  disabled?: boolean
  onSelect: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="menuitem"
      className={`mi${props.danger ? ' danger' : ''}`}
      disabled={props.disabled}
      onClick={props.onSelect}
    >
      {props.icon}
      <span className="mi-label">{props.children}</span>
      {props.note && <span className="mi-note">{props.note}</span>}
    </button>
  )
}

export function Swatches({ value, onPick }: { value: Colour; onPick: (c: Colour) => void }) {
  return (
    <div className="swatches" role="group" aria-label="Colour">
      {PALETTE.map((c) => (
        <button
          key={c}
          type="button"
          role="menuitemradio"
          aria-checked={c === value}
          aria-label={c}
          title={c}
          className={`sw c-${c}`}
          onClick={() => onPick(c)}
        />
      ))}
    </div>
  )
}

function preview(latex: string): string {
  return latex.trim() ? katex.renderToString(latex, { displayMode: true, throwOnError: false }) : ''
}

export function MathEditor(props: {
  anchor: HTMLElement
  latex: string
  onDone: (latex: string) => void
  onClose: () => void
}) {
  const [latex, setLatex] = useState(props.latex)
  const html = useMemo(() => preview(latex), [latex])
  return (
    <Popover anchor={props.anchor} label="Edit formula" role="dialog" className="math-pop" onClose={props.onClose}>
      <label htmlFor="math-latex">LaTeX</label>
      <textarea
        id="math-latex"
        spellCheck={false}
        value={latex}
        onChange={(e) => setLatex(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) props.onDone(latex)
        }}
      />
      <div className="math-preview" aria-live="polite" dangerouslySetInnerHTML={{ __html: html }} />
      <div className="row">
        <button type="button" className="btn btn-sm" onClick={props.onClose}>
          Cancel
        </button>
        <button type="button" className="btn btn-sm btn-primary" onClick={() => props.onDone(latex)}>
          Done
        </button>
      </div>
    </Popover>
  )
}
