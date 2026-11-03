import type { IrisEvent } from './client'

export const EVENT_TYPES: IrisEvent['type'][] = [
  'status', 'stage_started', 'stage_finished', 'transcript_chunk', 'moment_found',
  'visual_selected', 'notes_updated', 'error',
]

/** Distinguish server messages from EventSource transport errors and invalid payloads. */
export function decodeSessionEvent(event: Event, sessionId: string): IrisEvent | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== 'string') return null
  try {
    const message: unknown = JSON.parse(event.data)
    if (!message || typeof message !== 'object') return null
    const value = message as Partial<IrisEvent>
    if (
      value.type !== event.type || !EVENT_TYPES.includes(value.type as IrisEvent['type']) ||
      value.session_id !== sessionId || typeof value.at !== 'string' ||
      (value.data !== undefined &&
        (!value.data || typeof value.data !== 'object' || Array.isArray(value.data)))
    ) return null
    return value as IrisEvent
  } catch {
    return null
  }
}
