import type { Stage } from '../api/client'
import {
  STAGE_LABELS, STAGE_ORDER, stageDetail, stepStatus, type RunState, type StepStatus,
} from '../session/state'
import { Icon, type IconName } from './Icon'

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
 * Every pipeline step with its status and count, in order. A step done while recording (promoted
 * at stop) says "· live". One row per step whatever its state, so nothing moves as steps advance.
 */
export function StepList(props: {
  run: RunState
  /** Stored progress when no events are known (e.g. after a backend restart). */
  finished: boolean
  hasEvents: boolean
  currentStage?: Stage | null
  className?: string
}) {
  const { run } = props
  return (
    <ol className={props.className ? `steps ${props.className}` : 'steps'}>
      {STAGE_ORDER.map((stage) => {
        const s = stepStatus(stage, run, props)
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
  )
}
