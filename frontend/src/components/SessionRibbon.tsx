import { useState } from 'react'
import { formatClock, type FigureState } from '../session/state'

/**
 * The session ribbon (docs/frontend.md §5): the session's length as a line, with each figure marked
 * where it was shown. Hollow while being redrawn, filled when done, amber for an unverified crop.
 */
export function SessionRibbon(props: {
  length: number
  figures: FigureState[]
  onSelect: (figure: FigureState) => void
}) {
  const [hover, setHover] = useState<string | null>(null)
  const placed = props.figures.filter((f) => f.t !== undefined)

  return (
    <div className="ribbon">
      <div className="ribbon-track" role="group" aria-label="Figures by time in the session">
        {placed.map((f) => {
          const left = Math.min(100, Math.max(0, ((f.t ?? 0) / props.length) * 100))
          const state = !f.rendered ? 'Being redrawn' : f.verified === false ? 'Original crop, not verified' : 'Ready'
          return (
            <button
              key={f.id}
              type="button"
              className={`ribbon-mark${f.rendered ? ' done' : ''}${f.verified === false ? ' unverified' : ''}`}
              style={{ left: `${left}%` }}
              aria-label={`${f.caption || 'Figure'} at ${formatClock(f.t ?? 0)}. ${state}.`}
              onClick={() => props.onSelect(f)}
              onMouseEnter={() => setHover(f.id)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(f.id)}
              onBlur={() => setHover(null)}
            >
              {hover === f.id && (
                <span className="ribbon-tip" aria-hidden="true">
                  {formatClock(f.t ?? 0)} · {f.caption || 'Figure'}
                </span>
              )}
            </button>
          )
        })}
      </div>
      <div className="ribbon-scale num" aria-hidden="true">
        <span>0:00</span>
        <span>{formatClock(props.length)}</span>
      </div>
    </div>
  )
}
