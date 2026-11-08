import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { revealDelta } from './reveal'

export interface TabDef<T extends string> {
  id: T
  label: string
  count?: string
}

/** ARIA tabs: arrow keys, Home and End move between tabs; Tab moves on to `end`, if any, then
 * into the panel. */
export function Tabs<T extends string>(props: {
  tabs: TabDef<T>[]
  active: T
  onChange: (id: T) => void
  label: string
  idPrefix: string
  /** Shown at the end of the row, outside the tablist (which may hold only tabs). */
  end?: ReactNode
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const list = useRef<HTMLDivElement>(null)

  // On a phone the tabs can scroll sideways in their row (index.css): keep the chosen one in
  // view, however it was chosen. Only the row scrolls, never the page.
  useEffect(() => {
    const row = list.current
    const tab = refs.current[props.active]
    if (!row || !tab || row.scrollWidth <= row.clientWidth) return
    // Inside the row's padding, which is the room for the focus ring at either end.
    const box = row.getBoundingClientRect()
    const pad = getComputedStyle(row)
    const view = { left: box.left + parseFloat(pad.paddingLeft), right: box.right - parseFloat(pad.paddingRight) }
    const item = tab.getBoundingClientRect()
    row.scrollLeft += revealDelta({ left: item.left, right: item.right }, view)
  }, [props.active])

  function onKeyDown(e: KeyboardEvent, index: number) {
    const n = props.tabs.length
    const next =
      e.key === 'ArrowRight' ? (index + 1) % n
      : e.key === 'ArrowLeft' ? (index - 1 + n) % n
      : e.key === 'Home' ? 0
      : e.key === 'End' ? n - 1
      : -1
    if (next < 0) return
    e.preventDefault()
    const id = props.tabs[next].id
    props.onChange(id)
    refs.current[id]?.focus()
  }

  return (
    <div className="tabs">
      <div ref={list} className="tab-list" role="tablist" aria-label={props.label}>
        {props.tabs.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el
            }}
            type="button"
            role="tab"
            id={`${props.idPrefix}-tab-${t.id}`}
            aria-controls={`${props.idPrefix}-panel-${t.id}`}
            aria-selected={props.active === t.id}
            tabIndex={props.active === t.id ? 0 : -1}
            className="tab"
            onClick={() => props.onChange(t.id)}
            onKeyDown={(e) => onKeyDown(e, i)}
          >
            {t.label}
            {t.count && <span className="count num">{t.count}</span>}
          </button>
        ))}
      </div>
      {props.end}
    </div>
  )
}

/** A tab panel that stays mounted (keeps scroll and loaded images) while hidden. */
export function TabPanel(props: { id: string; idPrefix: string; active: boolean; children: ReactNode }) {
  return (
    <div
      role="tabpanel"
      id={`${props.idPrefix}-panel-${props.id}`}
      aria-labelledby={`${props.idPrefix}-tab-${props.id}`}
      hidden={!props.active}
      tabIndex={0}
      className="panel"
    >
      {props.children}
    </div>
  )
}
