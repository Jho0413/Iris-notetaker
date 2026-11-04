import { useState } from 'react'
import type { Stage } from '../api/client'
import { detailRows, type Cost, type StepCost } from '../session/cost'
import type { StreamStatus } from '../session/useSessionStream'
import { STAGE_ORDER, stepLine, stepStatus, type RunState } from '../session/state'
import { CostText } from './CostText'
import type { OutlineItem } from './NotesView'
import { StepList } from './StepList'

/**
 * The session rail: plain-language progress (once the notes are finished; until then the progress
 * view shows it), the notes outline, the cost slot and on-demand details. Below 1120px it
 * collapses into a one-line summary that expands.
 */
export function ProgressRail(props: {
  run: RunState
  /** Stored progress when no events are known (e.g. after a backend restart). */
  finished: boolean
  hasEvents: boolean
  currentStage?: Stage | null
  outline: OutlineItem[]
  stream: StreamStatus
  error?: string | null
  showProgress: boolean
  /** The session's model cost so far; null shows nothing (no calls yet, or not loaded). */
  cost: Cost | null
  /** Cost per step that made model calls, in pipeline order. */
  stepCosts: StepCost[]
}) {
  const [expanded, setExpanded] = useState(false)
  const { run } = props
  const stored = { finished: props.finished, hasEvents: props.hasEvents, currentStage: props.currentStage }

  // Without the progress list (it is in the main column until the notes are finished), the
  // rail holds only the outline and details.
  const running = STAGE_ORDER.filter((s) => stepStatus(s, run, stored) === 'running')
  const summary = !props.showProgress
    ? 'Run details'
    : running.length
      ? running.map((s) => stepLine(s, run)).join(' · ')
      : props.finished ? 'All steps finished' : 'Progress'
  const rows = detailRows(run, props.stepCosts)

  return (
    <aside className={`rail${expanded ? ' expanded' : ''}`} aria-label="Session progress and details">
      <div className="rail-summary">
        <span>{summary}</span>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-expanded={expanded}
          aria-controls="rail-full"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? 'Hide details' : 'Details'}
        </button>
      </div>
      <div className="rail-full" id="rail-full">
        {props.showProgress && (
          <>
            <h2>Progress</h2>
            <StepList run={run} {...stored} />
          </>
        )}

        {props.outline.length > 0 && (
          <>
            <h2>On this page</h2>
            <ul className="outline">
              {props.outline.map((item) => (
                <li key={item.id}>
                  <a href={`#${item.id}`}>{item.text}</a>
                </li>
              ))}
            </ul>
          </>
        )}

        {/* Always laid out, so the rail doesn't move when the first cost arrives. */}
        <p className="cost-slot" style={props.cost ? undefined : { visibility: 'hidden' }} aria-hidden={!props.cost}>
          <span>Cost</span> {props.cost && <CostText cost={props.cost} />}
        </p>

        <details className="details">
          <summary>Details</summary>
          {rows.length > 0 && (
            <table>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.key}>
                    <td>{row.label}</td>
                    <td className="num">
                      {row.cost && <CostText cost={row.cost} />}
                      {row.cost && row.time && ' · '}
                      {row.time}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {!rows.some((r) => r.time) && <p>Step times are only available for runs since the backend started.</p>}
          {props.cost?.partial && (
            <p>
              Costs marked ≥ leave out model calls Iris has no price for yet, so the real cost may
              be higher.
            </p>
          )}
          <p>
            Live updates:{' '}
            {props.stream === 'open' ? 'connected' : props.stream === 'connecting' ? 'waiting for the backend…' : 'reconnecting…'}
          </p>
          {props.error && <div className="raw">{props.error}</div>}
        </details>
      </div>
    </aside>
  )
}

