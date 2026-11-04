// Session view state: progress events reduced into what the session page shows.
//
// Pure functions only, so the logic is testable under plain Node (tests/sessionState.test.ts).
// Imports from the API layer are type-only for the same reason.
//
// The event stream replays a session's history on every (re)connect, so applying an event twice
// must change nothing: everything is keyed (stage name, transcript chunk index, figure id).
// A `status: processing` event marks the start of a run and clears the previous run's progress.
// Events older than the current run's start belong to an earlier run and are ignored, so replaying
// history (all runs, in order) always lands on the latest run.
import type { IrisEvent, Notes, Session, SessionStatus, Stage, Transcript } from '../api/client'

export type Segment = Transcript['segments'][number]
export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'stopped'

export const STAGE_ORDER: readonly Stage[] = [
  'ingest', 'transcribe', 'detect', 'align', 'select', 'write', 'render', 'assemble', 'synthesize',
]

/** Plain-language step names (docs/frontend.md §1), used everywhere in the UI, Details included. */
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
  /** Reported as promoted: its live work is being kept at stop instead of running the stage. */
  promoted?: boolean
  /**
   * Transcript segments, from a promoted transcribe step's `stage_finished`. Its parts were
   * transcribed while recording, before this run's counts were reset, so they can't be counted
   * from `transcript_chunk` events.
   */
  segments?: number
}

/** Done while recording: promoted at stop, and the promotion finished. */
export function doneLive(step: StageRun): boolean {
  return step.promoted === true && step.status === 'done'
}

export interface FigureState {
  id: string
  caption: string
  /** Seconds into the session where the figure was shown. */
  t?: number
  path?: string
  renderAs?: string
  verified?: boolean
  rendered: boolean
}

export interface RunState {
  /** Latest `status` event, if any arrived (none after a backend restart). */
  status?: SessionStatus
  stages: Record<Stage, StageRun>
  chunks: Record<number, Segment[]>
  moments: Record<string, { start: number; end: number }>
  figures: Record<string, FigureState>
  /** Figure ids in the order they were selected. */
  figureOrder: string[]
  /**
   * Notes progress from `notes_updated`: sections written (by the write stage, or ahead of stop
   * by the live draft lane while recording) and whether the notes are assembled (`final`). Only
   * counts: no notes are shown before the session is done.
   */
  notes: { final: boolean; completed: number; total: number } | null
  error?: string
  /** When the current run started (its `status: processing` event), in ms since the epoch. */
  runStart?: number
}

export function emptyRun(): RunState {
  const stages = {} as Record<Stage, StageRun>
  for (const s of STAGE_ORDER) stages[s] = { status: 'pending' }
  return { stages, chunks: {}, moments: {}, figures: {}, figureOrder: [], notes: null }
}

const STATUSES: readonly string[] = ['created', 'recording', 'processing', 'done', 'failed']

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

/** The stage named by a runner error such as "write failed: TimeoutError: …". */
export function stageFromError(message: string | null | undefined): Stage | undefined {
  const name = message?.match(/^(\w+) failed:/)?.[1]
  return isStage(name) ? name : undefined
}

function upsertFigure(state: RunState, id: string, patch: Partial<FigureState>): RunState {
  const prev = state.figures[id]
  const next: FigureState = { ...(prev ?? { id, caption: '', rendered: false }), ...patch }
  // A rendered figure never goes back to "redrawing" when its selection event is replayed.
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
  // Parsed, not compared as strings: the backend omits zero microseconds from timestamps.
  const at = Date.parse(event.at)
  const timed = Number.isFinite(at)
  if (timed && state.runStart !== undefined && at < state.runStart) return state
  switch (event.type) {
    case 'status': {
      const status = str(data.status)
      if (!status || !STATUSES.includes(status)) return state
      // A new run starts with `processing`. It can follow a run that never finished (the backend
      // restarted mid-run), so compare timestamps rather than the previous status.
      const newRun = timed
        ? state.runStart === undefined || at > state.runStart
        : state.status !== 'processing' // unparseable time: fall back to the status change
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
      const message = str(data.message)
      const failed = stageFromError(message)
      const stages = { ...state.stages }
      for (const s of STAGE_ORDER) {
        if (stages[s].status !== 'running') continue
        stages[s] = { ...stages[s], status: s === failed || !failed ? 'failed' : 'stopped' }
      }
      if (failed && stages[failed].status === 'pending') {
        stages[failed] = { ...stages[failed], status: 'failed' }
      }
      return { ...state, stages, error: message }
    }
    default:
      return state
  }
}

/** Identity of an event, used to drop the history the backend replays on every reconnect. */
export function eventKey(event: IrisEvent): string {
  return `${event.type}|${event.at}|${JSON.stringify(event.data ?? {})}`
}

export function reduceEvents(events: readonly IrisEvent[]): RunState {
  return events.reduce(applyEvent, emptyRun())
}

// --- Derived views ------------------------------------------------------------------------------

/** Transcript to show: the full transcript once it exists, otherwise the chunks received so far. */
export function transcriptSegments(run: RunState, transcript: Transcript | null): Segment[] {
  if (transcript) return transcript.segments
  return Object.keys(run.chunks)
    .map(Number)
    .sort((a, b) => a - b)
    .flatMap((i) => run.chunks[i])
}

/** Figures in selection order, merging the finished notes (REST) with this run's events. */
export function figureList(run: RunState, notes: Notes | null): FigureState[] {
  const out = new Map<string, FigureState>()
  for (const v of notes?.visuals ?? []) {
    out.set(v.id, {
      id: v.id,
      caption: v.caption,
      t: v.frame.t,
      path: v.path,
      renderAs: v.render_as,
      // A redraw only reaches the notes after passing its check. A screenshot may be requested
      // or a fallback; only this run's events can tell which.
      verified: v.render_as === 'screenshot' ? undefined : true,
      rendered: true,
    })
  }
  for (const id of run.figureOrder) {
    const live = run.figures[id]
    const known = out.get(id)
    out.set(id, known
      ? { ...live, ...known, verified: live.verified ?? known.verified, t: known.t ?? live.t }
      : live)
  }
  return [...out.values()]
}

export type Provenance = 'verified' | 'screenshot' | 'unverified'

/**
 * How a figure was produced, when it is known (docs/frontend.md §5, figures). A screenshot in the
 * finished notes may be a requested one or a fallback crop that failed its check; without this
 * run's events the two can't be told apart, so neither label is claimed.
 */
export function provenance(figure: FigureState): Provenance | undefined {
  if (!figure.rendered || figure.verified === undefined) return undefined
  if (!figure.verified) return 'unverified'
  return figure.renderAs === 'screenshot' ? 'screenshot' : 'verified'
}

/** Session length in seconds, from the transcript (the backend doesn't set `duration_s` yet). */
export function sessionLength(session: Session, segs: readonly Segment[]): number | undefined {
  if (session.duration_s) return session.duration_s
  const end = segs.reduce((max, s) => Math.max(max, s.end), 0)
  return end > 0 ? end : undefined
}

export type UiStateKind =
  | 'recording'
  | 'recording-orphan'
  | 'starting'
  | 'processing'
  | 'polishing'
  | 'stalled'
  | 'finished'
  | 'failed'

export interface UiState {
  kind: UiStateKind
  /** Short label for the status chip. */
  label: string
  /** The step a retry or resume starts from. */
  fromStage?: Stage
  /** While processing: the running step the label names. */
  stage?: Stage
}

/** Combine the stored status with what the page knows (docs/frontend.md §3). */
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
    case 'created':
      return { kind: 'starting', label: 'Starting…' }
    case 'done':
      return { kind: 'finished', label: 'Finished' }
    case 'failed': {
      const stage =
        stageFromError(session.error ?? run.error) ??
        STAGE_ORDER.find((s) => run.stages[s].status === 'failed') ??
        session.current_stage ??
        undefined
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
      // Notes are assembled (shown only once done), or the synthesis pass is polishing them.
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

/**
 * What a screen reader is told when the state changes: the state and the step, never the
 * counts, so advancing counts aren't announced one by one.
 */
export function announcement(ui: UiState): string {
  if (ui.kind === 'processing') return ui.stage ? `Processing · ${STAGE_LABELS[ui.stage]}` : 'Processing'
  return ui.label
}

/**
 * The steps running now, in pipeline order (write and render run side by side). Without events
 * for one (after a backend restart, or between two steps) the stored current step stands in.
 */
export function currentSteps(session: Session, run: RunState): Stage[] {
  const running = STAGE_ORDER.filter((s) => run.stages[s].status === 'running')
  if (running.length) return running
  return session.current_stage ? [session.current_stage] : []
}

/** A step with its count, e.g. "Redrawing figures · 2 of 3". */
export function stepLine(stage: Stage, run: RunState): string {
  return [STAGE_LABELS[stage], stageDetail(stage, run)].filter(Boolean).join(' · ')
}

/**
 * A step's status in the progress list. Without any events (the backend restarted, so its
 * history is gone) it is inferred from the stored session: all done once finished, otherwise
 * the steps before the stored current one.
 */
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
  /**
   * The notes are being made: show the current step, how long processing has run and that it's
   * fine to leave the page. Otherwise (failed, stalled, never finished recording) the page's
   * banner says what happened and what to do.
   */
  active: boolean
}

/**
 * What the session page shows in place of the notes until they're finished (docs/frontend.md
 * §2, Session). Null once finished: then the notes are shown.
 */
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
      return { heading: 'No notes yet', active: false }
  }
}

/** Seconds since the current run started, when its start is known. */
export function runSeconds(run: RunState, now: number): number | undefined {
  return run.runStart === undefined ? undefined : Math.max(0, (now - run.runStart) / 1000)
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/**
 * The Live page's progress line, e.g. "Transcribed to 4:30 · 3 screens · 2 figures read ·
 * 1 section written", from the recording's events. Only the parts that are non-zero; an empty
 * string until something is processed.
 */
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

/** Label for a library row, where only the stored session is known. */
export function libraryState(session: Session): UiState {
  return uiState(session, emptyRun())
}

/**
 * Count shown beside a running or finished step, e.g. "3 of 5". A step done while recording
 * (promoted at stop) says so: "4 moments · live".
 */
export function stageDetail(stage: Stage, run: RunState): string | undefined {
  const count = stageCount(stage, run)
  if (!doneLive(run.stages[stage])) return count
  return count ? `${count} · live` : 'live'
}

function stageCount(stage: Stage, run: RunState): string | undefined {
  switch (stage) {
    case 'transcribe': {
      // Promoted at stop: the backend reports the transcript's size (see `StageRun.segments`).
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

/** Seconds a step took, when both of its events are known (not for a step done while recording). */
export function stageSeconds(step: StageRun): number | undefined {
  if (doneLive(step) || !step.startedAt || !step.finishedAt) return undefined
  const ms = Date.parse(step.finishedAt) - Date.parse(step.startedAt)
  return Number.isFinite(ms) && ms >= 0 ? ms / 1000 : undefined
}

/** "4:05" or "1:02:09". */
export function formatClock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** "0.8 s", "42 s", "3 min 05 s". */
export function formatDuration(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)} s`
  if (seconds < 60) return `${Math.round(seconds)} s`
  const m = Math.floor(seconds / 60)
  return `${m} min ${String(Math.round(seconds % 60)).padStart(2, '0')} s`
}

/** Plain-language headline for a failure: "Writing notes failed". The raw text goes in Details. */
export function failureHeadline(message: string | null | undefined): string {
  const stage = stageFromError(message)
  return stage ? `${STAGE_LABELS[stage]} failed` : 'Processing failed'
}
