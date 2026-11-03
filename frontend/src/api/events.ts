import { useEffect, useState } from 'react'
import type { IrisEvent } from './client'
import { decodeSessionEvent, EVENT_TYPES } from './eventMessage'

/**
 * Subscribe to a session's progress events (Server-Sent Events). The backend replays recent
 * history on connect, so the returned list is complete even if the page loads mid-run.
 */
export function useSessionEvents(sessionId: string | undefined): IrisEvent[] {
  const [state, setState] = useState<{ id?: string; events: IrisEvent[] }>({ events: [] })

  useEffect(() => {
    if (!sessionId) return
    const source = new EventSource(`/api/sessions/${sessionId}/events`)
    const onEvent = (event: Event) => {
      // Transport errors also use the name "error", but carry no application payload.
      // Parse before updating state so malformed messages cannot crash a React render.
      const message = decodeSessionEvent(event, sessionId)
      if (!message) return
      setState((prev) => ({
        id: sessionId,
        events: prev.id === sessionId ? [...prev.events, message] : [message],
      }))
    }
    EVENT_TYPES.forEach((t) => source.addEventListener(t, onEvent))
    return () => source.close()
  }, [sessionId])

  return state.id === sessionId ? state.events : []
}
