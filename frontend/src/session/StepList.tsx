import type { Stage } from '../api/client'
import {
  STAGE_LABELS, STAGE_ORDER, stageDetail, stepStatus, type RunState, type StepStatus,
} from './state'
import { Icon, type IconName } from '../components/Icon'

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

export function StepList(props: {
  run: RunState
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
