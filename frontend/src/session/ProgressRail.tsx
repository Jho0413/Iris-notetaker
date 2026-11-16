import type { Stage } from '../api/client'
import { detailRows, type Cost, type StepCost } from './cost'
import { detailsLabel } from '../lib/messages'
import type { StreamStatus } from './useSessionStream'
import { STAGE_ORDER, stepLine, stepStatus, type RunState } from './state'
import { CostText } from './CostText'
import type { OutlineItem } from '../lib/notes'
import { StepList } from './StepList'

export interface RunDetailsProps {
  run: RunState
  finished: boolean
  hasEvents: boolean
  currentStage?: Stage | null
  outline: OutlineItem[]
  stream: StreamStatus
  showProgress: boolean
  cost: Cost | null
  stepCosts: StepCost[]
}

export function ProgressRail(
  props: RunDetailsProps & {
    expanded: boolean
    onToggle: () => void
    detailsInTabs: boolean
  },
) {
  const { run, expanded } = props
  const stored = { finished: props.finished, hasEvents: props.hasEvents, currentStage: props.currentStage }

  const running = STAGE_ORDER.filter((s) => stepStatus(s, run, stored) === 'running')
  const summary = !props.showProgress
    ? 'Run details'
    : running.length
      ? running.map((s) => stepLine(s, run)).join(' · ')
      : props.finished ? 'All steps finished' : 'Progress'

  return (
    <aside
      className={`rail${expanded ? ' expanded' : ''}${props.detailsInTabs ? ' rail-tabbed' : ''}`}
      aria-label="Session progress and details"
    >
      <div className="rail-summary">
        <span>{summary}</span>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          aria-expanded={expanded}
          aria-controls="rail-full"
          onClick={props.onToggle}
        >
          {detailsLabel(expanded)}
        </button>
      </div>
      <div className="rail-full" id="rail-full">
        <RunDetails {...props} />
      </div>
    </aside>
  )
}

export function RunDetails(props: RunDetailsProps) {
  const { run } = props
  const stored = { finished: props.finished, hasEvents: props.hasEvents, currentStage: props.currentStage }
  const rows = detailRows(run, props.stepCosts)
  return (
    <div className="run-details">
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
        {!rows.some((r) => r.time) && <p>Step times are only available for runs since Iris started.</p>}
        {props.cost?.partial && (
          <p>
            Costs marked ≥ leave out model calls Iris has no price for yet, so the real cost may
            be higher.
          </p>
        )}
        <p>
          Live updates:{' '}
          {props.stream === 'open' ? 'connected' : props.stream === 'connecting' ? 'waiting for Iris…' : 'reconnecting…'}
        </p>
      </details>
    </div>
  )
}
