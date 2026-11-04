import type { UiState } from '../session/state'
import { Icon } from './Icon'

/** Session status in words, with colour and glyph as secondary signals. */
export function StatusChip({ state }: { state: UiState }) {
  const kind = state.kind === 'recording-orphan' || state.kind === 'stalled' ? 'neutral' : state.kind
  return (
    <span className={`chip chip-${kind}`}>
      {state.kind === 'recording' && <span className="dot" aria-hidden="true" />}
      {(state.kind === 'processing' || state.kind === 'starting' || state.kind === 'polishing') && (
        <Icon name="spinner" />
      )}
      {state.kind === 'finished' && <Icon name="check" />}
      {(state.kind === 'failed' || state.kind === 'stalled') && <Icon name="alert" />}
      {state.label}
    </span>
  )
}
