import { useState } from 'react'
import type { Stage } from '../api/client'
import type { StreamStatus } from '../session/useSessionStream'
import {
  doneLive, formatDuration, STAGE_LABELS, STAGE_ORDER, stageDetail, stageSeconds, type RunState,
  type StepStatus,
} from '../session/state'
import { Icon, type IconName } from './Icon'
import type { OutlineItem } from './NotesView'

const STEP_ICON: Record<StepStatus, IconName> = {
  pending: 'pending',
  running: 'spinner',
  done: 'check',
  failed: 'alert',
  stopped: 'stopped',
}

const STEP_WORD: Record<StepStatus, string> = {
  pending: 'Not started',
  running: 'In progress',
  done: 'Done',
  failed: 'Failed',
  stopped: 'Stopped',
}

/**
 * The session rail: plain-language progress, the notes outline, the cost slot and on-demand
 * details. Below 1120px it collapses into a one-line summary that expands.
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
}) {
  const [expanded, setExpanded] = useState(false)
  const { run } = props

  function status(stage: Stage): StepStatus {
    const s = run.stages[stage].status
    if (s !== 'pending' || props.hasEvents) return s
    // No events: infer from the stored session.
    if (props.finished) return 'done'
    const current = props.currentStage ? STAGE_ORDER.indexOf(props.currentStage) : -1
    const i = STAGE_ORDER.indexOf(stage)
    return i < current ? 'done' : 'pending'
  }

  const running = STAGE_ORDER.filter((s) => status(s) === 'running')
  const summary = running.length
    ? running.map((s) => [STAGE_LABELS[s], stageDetail(s, run)].filter(Boolean).join(' ')).join(' · ')
    : props.finished ? 'All steps finished' : 'Progress'
  const timed = STAGE_ORDER.filter(
    (s) => doneLive(run.stages[s]) || stageSeconds(run.stages[s]) !== undefined,
  )

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
            <ol className="steps">
              {STAGE_ORDER.map((stage) => {
                const s = status(stage)
                const detail = stageDetail(stage, run)
                return (
                  <li key={stage} className={`step step-${s}`}>
                    <Icon name={STEP_ICON[s]} label={STEP_WORD[s]} />
                    <span>{STAGE_LABELS[stage]}</span>
                    {detail && <span className="step-detail num">{detail}</span>}
                  </li>
                )
              })}
            </ol>
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

        {/* Cost slot (docs/frontend.md §7): built once the cost-tracking API is merged. */}

        <details className="details">
          <summary>Details</summary>
          {timed.length > 0 ? (
            <table>
              <tbody>
                {timed.map((s) => (
                  <tr key={s}>
                    <td>
                      <code>{s}</code>
                    </td>
                    <td className="num">
                      {doneLive(run.stages[s])
                        ? 'while recording'
                        : formatDuration(stageSeconds(run.stages[s])!)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p>Step times are only available for runs since the backend started.</p>
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
