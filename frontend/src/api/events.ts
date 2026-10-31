import { useEffect, useState } from 'react'
import type { IrisEvent } from './client'

const EVENT_TYPES: IrisEvent['type'][] = [
  'status',
  'stage_started',
  'stage_finished',
  'transcript_chunk',
  'moment_found',
  'visual_selected',
  'notes_updated',
  'error',
]

/**
 * Subscribe to a session's progress events (Server-Sent Events). The backend replays recent
 * history on connect, so the returned list is complete even if the page loads mid-run.
 */
export function useSessionEvents(sessionId: string | undefined): IrisEvent[] {
  const [state, setState] = useState<{ id?: string; events: IrisEvent[] }>({ events: [] })

  useEffect(() => {
    if (!sessionId) return
    const source = new EventSource(`/api/sessions/${sessionId}/events`)
    const onEvent = (e: MessageEvent) =>
      setState((prev) => ({
        id: sessionId,
        events: prev.id === sessionId ? [...prev.events, JSON.parse(e.data)] : [JSON.parse(e.data)],
      }))
    EVENT_TYPES.forEach((t) => source.addEventListener(t, onEvent))
    return () => source.close()
  }, [sessionId])

  return state.id === sessionId ? state.events : []
}
