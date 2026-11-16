import type { IrisEvent, Notes, Session, SessionStatus, Stage, Transcript } from '../api/client'
import { formatClock } from '../lib/format.ts'
import { notesFigures, type FigureState } from '../lib/notes.ts'

export type FailureMessage = NonNullable<Session['failure_message']>
export type FailureKind = NonNullable<Session['failure_kind']>

export const GENERIC_FAILURE: FailureMessage =
  "Iris couldn't finish making the notes. Try again; finished steps are kept."

const FAILURE_MESSAGES: Record<FailureMessage, true> = {
  "Iris couldn't finish making the notes. Try again; finished steps are kept.": true,
  "Iris couldn't read this file. Upload an audio or video recording.": true,
  'This recording has no sound. Upload a recording with speech.': true,
  'No audio was recorded. Check that the microphone works, then record again.': true,
  "The recorded audio couldn't be read. Record the session again.": true,
  'Iris found no speech or screen content in this recording to make notes from.': true,
  'Iris was closed before these notes were finished. Try again; finished steps are kept.': true,
}

export function knownFailureMessage(message: unknown): FailureMessage {
  return typeof message === 'string' && Object.hasOwn(FAILURE_MESSAGES, message)
    ? (message as FailureMessage)
    : GENERIC_FAILURE
}

function knownFailureKind(kind: unknown, message: FailureMessage): FailureKind {
  return kind === 'input' && message !== GENERIC_FAILURE ? 'input' : 'general'
}

export interface Failure {
  message: FailureMessage
  kind: FailureKind
  stage?: Stage
}

export type Segment = Transcript['segments'][number]
export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'stopped'

export const STAGE_ORDER: readonly Stage[] = [
  'ingest', 'transcribe', 'detect', 'align', 'select', 'write', 'render', 'assemble', 'synthesize',
]

export const STAGE_LABELS: Record<Stage, string> = {
  ingest: 'Preparing the recording',
  transcribe: 'Transcribing',
  detect: 'Finding what was shown',
  align: 'Matching speech to the screen',
  select: 'Reading the screen',
  write: 'Writing notes',
  render: 'Redrawing figures',
  assemble: 'Putting the notes together',
  synthesize: 'Polishing notes',
}

export interface StageRun {
  status: StepStatus
  startedAt?: string
  finishedAt?: string
  promoted?: boolean
  segments?: number
}

export function doneLive(step: StageRun): boolean {
  return step.promoted === true && step.status === 'done'
}

export interface RunState {
  status?: SessionStatus
  stages: Record<Stage, StageRun>
  chunks: Record<number, Segment[]>
  moments: Record<string, { start: number; end: number }>
  figures: Record<string, FigureState>
  figureOrder: string[]
  notes: { final: boolean; completed: number; total: number } | null
  failure?: Failure
  runStart?: number
}

export function emptyRun(): RunState {
  const stages = {} as Record<Stage, StageRun>
  for (const s of STAGE_ORDER) stages[s] = { status: 'pending' }
  return { stages, chunks: {}, moments: {}, figures: {}, figureOrder: [], notes: null }
}

const STATUSES: readonly string[] = ['created', 'recording', 'stopped', 'processing', 'done', 'failed']

function isStage(value: unknown): value is Stage {
  return typeof value === 'string' && (STAGE_ORDER as readonly string[]).includes(value)
}

function num(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function segments(value: unknown): Segment[] {
  if (!Array.isArray(value)) return []
  return value.flatMap((s): Segment[] => {
    if (!s || typeof s !== 'object') return []
    const { start, end, text } = s as Record<string, unknown>
    const a = num(start)
    const b = num(end)
    return a !== undefined && b !== undefined && typeof text === 'string'
      ? [{ start: a, end: b, text }]
      : []
  })
}

function upsertFigure(state: RunState, id: string, patch: Partial<FigureState>): RunState {
  const prev = state.figures[id]
  const next: FigureState = { ...(prev ?? { id, caption: '', rendered: false }), ...patch }
  if (prev?.rendered) {
    next.rendered = true
    next.path = patch.rendered ? patch.path ?? prev.path : prev.path
  }
  return {
    ...state,
    figures: { ...state.figures, [id]: next },
    figureOrder: state.figureOrder.includes(id) ? state.figureOrder : [...state.figureOrder, id],
  }
}

export function applyEvent(state: RunState, event: IrisEvent): RunState {
  const data = (event.data ?? {}) as Record<string, unknown>
  const at = Date.parse(event.at)
  const timed = Number.isFinite(at)
  if (timed && state.runStart !== undefined && at < state.runStart) return state
  switch (event.type) {
    case 'status': {
      const status = str(data.status)
      if (!status || !STATUSES.includes(status)) return state
      const newRun = timed
        ? state.runStart === undefined || at > state.runStart
        : state.status !== 'processing'
      if (status === 'processing' && newRun) {
        return { ...emptyRun(), status: 'processing', runStart: timed ? at : state.runStart }
      }
      return { ...state, status: status as SessionStatus }
    }
    case 'stage_started':
    case 'stage_finished': {
      if (!isStage(data.stage)) return state
      const prev = state.stages[data.stage]
      const promoted = data.promoted === true ? { promoted: true } : {}
      const count = num(data.segments)
      const next: StageRun =
        event.type === 'stage_started'
          ? { ...prev, ...promoted, status: prev.status === 'done' ? 'done' : 'running', startedAt: event.at }
          : {
              ...prev,
              ...promoted,
              ...(count !== undefined && count >= 0 ? { segments: Math.floor(count) } : {}),
              status: 'done',
              finishedAt: event.at,
            }
      return { ...state, stages: { ...state.stages, [data.stage]: next } }
    }
    case 'transcript_chunk': {
      const index = num(data.chunk)
      if (index === undefined) return state
      return { ...state, chunks: { ...state.chunks, [index]: segments(data.segments) } }
    }
    case 'moment_found': {
      const id = str(data.id)
      const start = num(data.start)
      const end = num(data.end)
      if (!id || start === undefined || end === undefined) return state
      return { ...state, moments: { ...state.moments, [id]: { start, end } } }
    }
    case 'visual_selected': {
      const id = str(data.id)
      if (!id) return state
      const frame = data.frame as Record<string, unknown> | undefined
      return upsertFigure(state, id, {
        caption: str(data.caption) ?? '',
        t: num(frame?.t),
        renderAs: str(data.render_as),
      })
    }
    case 'visual_rendered': {
      const id = str(data.id)
      const path = str(data.path)
      if (!id || !path) return state
      return upsertFigure(state, id, {
        path,
        renderAs: str(data.render_as),
        verified: typeof data.verified === 'boolean' ? data.verified : undefined,
        rendered: true,
      })
    }
    case 'notes_updated': {
      const final = data.final === true
      const completed = num(data.completed_parts) ?? state.notes?.completed ?? 0
      const total = num(data.total_parts) ?? state.notes?.total ?? 0
      return {
        ...state,
        notes: {
          final: final || state.notes?.final === true,
          completed: Math.max(completed, state.notes?.completed ?? 0),
          total: Math.max(total, state.notes?.total ?? 0),
        },
      }
    }
    case 'error': {
      const message = knownFailureMessage(data.message)
      const failed = isStage(data.stage) ? data.stage : undefined
      const stages = { ...state.stages }
      for (const s of STAGE_ORDER) {
        if (stages[s].status !== 'running') continue
        stages[s] = { ...stages[s], status: s === failed || !failed ? 'failed' : 'stopped' }
      }
      if (failed && stages[failed].status === 'pending') {
        stages[failed] = { ...stages[failed], status: 'failed' }
      }
      const failure: Failure = { message, kind: knownFailureKind(data.kind, message), stage: failed }
      return { ...state, stages, failure }
    }
    default:
      return state
  }
}

export function eventKey(event: IrisEvent): string {
  return `${event.type}|${event.at}|${JSON.stringify(event.data ?? {})}`
}

export function reduceEvents(events: readonly IrisEvent[]): RunState {
  return events.reduce(applyEvent, emptyRun())
}

export function transcriptSegments(run: RunState, transcript: Transcript | null): Segment[] {
  if (transcript) return transcript.segments
  return Object.keys(run.chunks)
    .map(Number)
    .sort((a, b) => a - b)
    .flatMap((i) => run.chunks[i])
}

export function figureList(run: RunState, notes: Notes | null): FigureState[] {
  const out = new Map(notesFigures(notes).map((f) => [f.id, f] as const))
  for (const id of run.figureOrder) {
    const live = run.figures[id]
    const known = out.get(id)
    out.set(id, known
      ? { ...live, ...known, verified: live.verified ?? known.verified, t: known.t ?? live.t }
      : live)
  }
  return [...out.values()]
}

export type UiStateKind =
  | 'recording'
  | 'recording-orphan'
  | 'stopped'
  | 'starting'
  | 'processing'
  | 'polishing'
  | 'stalled'
  | 'finished'
  | 'failed'

export interface UiState {
  kind: UiStateKind
  label: string
  fromStage?: Stage
  stage?: Stage
}

export function uiState(
  session: Session,
  run: RunState,
  page: { capturing?: boolean; stalled?: boolean } = {},
): UiState {
  const status = run.status ?? session.status
  switch (status) {
    case 'recording':
      return page.capturing
        ? { kind: 'recording', label: 'Recording' }
        : { kind: 'recording-orphan', label: 'Recording not stopped' }
    case 'stopped':
      return { kind: 'stopped', label: 'Stopped' }
    case 'created':
      return { kind: 'starting', label: 'Starting…' }
    case 'done':
      return { kind: 'finished', label: 'Finished' }
    case 'failed': {
      const stage = failedStage(session, run)
      return {
        kind: 'failed',
        label: stage ? `Failed · ${STAGE_LABELS[stage]}` : 'Failed',
        fromStage: stage,
      }
    }
    default: {
      if (page.stalled) {
        return {
          kind: 'stalled',
          label: 'Stopped unexpectedly',
          fromStage: session.current_stage ?? undefined,
        }
      }
      const steps = currentSteps(session, run)
      if (run.notes?.final || steps.includes('synthesize')) {
        return { kind: 'polishing', label: STAGE_LABELS.synthesize, stage: 'synthesize' }
      }
      const stage = steps[steps.length - 1]
      return stage
        ? { kind: 'processing', label: stepLine(stage, run), stage }
        : { kind: 'processing', label: 'Processing' }
    }
  }
}

export function announcement(ui: UiState): string {
  if (ui.kind === 'processing') return ui.stage ? `Processing · ${STAGE_LABELS[ui.stage]}` : 'Processing'
  return ui.label
}

export function currentSteps(session: Session, run: RunState): Stage[] {
  const running = STAGE_ORDER.filter((s) => run.stages[s].status === 'running')
  if (running.length) return running
  return session.current_stage ? [session.current_stage] : []
}

export function stepLine(stage: Stage, run: RunState): string {
  return [STAGE_LABELS[stage], stageDetail(stage, run)].filter(Boolean).join(' · ')
}

export function stepStatus(
  stage: Stage,
  run: RunState,
  stored: { finished: boolean; hasEvents: boolean; currentStage?: Stage | null },
): StepStatus {
  const s = run.stages[stage].status
  if (s !== 'pending' || stored.hasEvents) return s
  if (stored.finished) return 'done'
  const current = stored.currentStage ? STAGE_ORDER.indexOf(stored.currentStage) : -1
  return STAGE_ORDER.indexOf(stage) < current ? 'done' : 'pending'
}

export interface ProgressView {
  heading: string
  active: boolean
}

export function progressView(kind: UiStateKind): ProgressView | null {
  switch (kind) {
    case 'finished':
      return null
    case 'starting':
      return { heading: 'Getting ready to make your notes', active: true }
    case 'processing':
      return { heading: 'Making your notes', active: true }
    case 'polishing':
      return { heading: 'Polishing your notes', active: true }
    case 'failed':
    case 'stalled':
      return { heading: "Your notes aren't finished", active: false }
    case 'recording':
    case 'recording-orphan':
    case 'stopped':
      return { heading: 'No notes yet', active: false }
  }
}

export function runSeconds(run: RunState, now: number): number | undefined {
  return run.runStart === undefined ? undefined : Math.max(0, (now - run.runStart) / 1000)
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

export function liveProgress(run: RunState): string {
  const to = transcriptSegments(run, null).reduce((max, s) => Math.max(max, s.end), 0)
  const screens = Object.keys(run.moments).length
  const figures = run.figureOrder.length
  const sections = run.notes?.completed ?? 0
  return [
    to > 0 && `Transcribed to ${formatClock(to)}`,
    screens > 0 && plural(screens, 'screen', 'screens'),
    figures > 0 && `${plural(figures, 'figure', 'figures')} read`,
    sections > 0 && `${plural(sections, 'section', 'sections')} written`,
  ]
    .filter(Boolean)
    .join(' · ')
}

export function libraryState(session: Session): UiState {
  return uiState(session, emptyRun())
}

export function stageDetail(stage: Stage, run: RunState): string | undefined {
  const count = stageCount(stage, run)
  if (!doneLive(run.stages[stage])) return count
  return count ? `${count} · live` : 'live'
}

function stageCount(stage: Stage, run: RunState): string | undefined {
  switch (stage) {
    case 'transcribe': {
      const segments = run.stages.transcribe.segments
      if (segments !== undefined) {
        return segments ? `${segments} ${segments === 1 ? 'segment' : 'segments'}` : undefined
      }
      const n = Object.keys(run.chunks).length
      return n ? `${n} ${n === 1 ? 'part' : 'parts'}` : undefined
    }
    case 'detect': {
      const n = Object.keys(run.moments).length
      return n ? `${n} ${n === 1 ? 'moment' : 'moments'}` : undefined
    }
    case 'select': {
      const n = run.figureOrder.length
      return n ? `${n} ${n === 1 ? 'figure' : 'figures'}` : undefined
    }
    case 'write':
      return run.notes?.total ? `${run.notes.completed} of ${run.notes.total}` : undefined
    case 'render': {
      const total = run.figureOrder.length
      const done = run.figureOrder.filter((id) => run.figures[id].rendered).length
      return total ? `${done} of ${total}` : undefined
    }
    default:
      return undefined
  }
}

export function stageSeconds(step: StageRun): number | undefined {
  if (doneLive(step) || !step.startedAt || !step.finishedAt) return undefined
  const ms = Date.parse(step.finishedAt) - Date.parse(step.startedAt)
  return Number.isFinite(ms) && ms >= 0 ? ms / 1000 : undefined
}

function failedStage(session: Session, run: RunState): Stage | undefined {
  return (
    run.failure?.stage ??
    STAGE_ORDER.find((s) => run.stages[s].status === 'failed') ??
    session.current_stage ??
    undefined
  )
}

export function failureView(session: Session, run: RunState): Failure & { headline: string } {
  const message = knownFailureMessage(run.failure?.message ?? session.failure_message)
  const kind = run.failure ? run.failure.kind : knownFailureKind(session.failure_kind, message)
  const stage = failedStage(session, run)
  const headline =
    kind === 'input'
      ? "Iris couldn't use this recording"
      : stage ? `Stopped at ${STAGE_LABELS[stage]}` : "Iris couldn't finish the notes"
  return { message, kind, stage, headline }
}
