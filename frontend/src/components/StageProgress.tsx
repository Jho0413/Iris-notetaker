import { STAGES, type IrisEvent, type Stage } from '../api/client'

type StageState = 'pending' | 'running' | 'done' | 'failed'

export function StageProgress({ events }: { events: IrisEvent[] }) {
  const state = new Map<Stage, StageState>(STAGES.map((s) => [s, 'pending']))
  let running: Stage | undefined
  for (const e of events) {
    const stage = e.data?.stage as Stage | undefined
    if (e.type === 'stage_started' && stage) {
      state.set(stage, 'running')
      running = stage
    }
    if (e.type === 'stage_finished' && stage) state.set(stage, 'done')
    if (e.type === 'error' && running) state.set(running, 'failed')
  }

  return (
    <ol className="stages">
      {STAGES.map((s) => (
        <li key={s} className={`stage stage-${state.get(s)}`}>
          {s}
        </li>
      ))}
    </ol>
  )
}
