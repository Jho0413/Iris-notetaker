import { useEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { revealDelta } from './reveal'

export interface TabDef<T extends string> {
  id: T
  label: string
  count?: string
}

export function Tabs<T extends string>(props: {
  tabs: TabDef<T>[]
  active: T
  onChange: (id: T) => void
  label: string
  idPrefix: string
  end?: ReactNode
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const list = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const row = list.current
    const tab = refs.current[props.active]
    if (!row || !tab || row.scrollWidth <= row.clientWidth) return
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
