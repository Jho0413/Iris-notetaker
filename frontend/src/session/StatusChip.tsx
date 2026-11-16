import type { UiState } from './state'
import { Icon } from '../components/Icon'

/** Session status in words, with colour and glyph as secondary signals. */
export function StatusChip({ state }: { state: UiState }) {
  const neutral = state.kind === 'recording-orphan' || state.kind === 'stalled' || state.kind === 'stopped'
  const kind = neutral ? 'neutral' : state.kind
  return (
    <span className={`chip chip-${kind}`}>
      {state.kind === 'recording' && <span className="dot" aria-hidden="true" />}
      {(state.kind === 'processing' || state.kind === 'starting' || state.kind === 'polishing') && (
        <Icon name="spinner" />
      )}
      {state.kind === 'finished' && <Icon name="check" />}
      {state.kind === 'stopped' && <Icon name="stopped" />}
      {(state.kind === 'failed' || state.kind === 'stalled') && <Icon name="alert" />}
      {/* Its own box so a long label can end with an ellipsis where space is short. */}
      <span className="chip-label">{state.label}</span>
    </span>
  )
}
