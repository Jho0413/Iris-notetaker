import { useEffect, useState } from 'react'
import type { IrisEvent } from '../api/client'
import { decodeSessionEvent, EVENT_TYPES } from '../api/eventMessage'
import { eventKey } from './state'

export type StreamStatus = 'connecting' | 'open' | 'reconnecting'

export interface SessionStream {
  events: IrisEvent[]
  status: StreamStatus
  /**
   * True when a connection attempt delivered no events within QUIET_AFTER_MS. The backend replays a
   * running session's history immediately, so silence means no run is active in this backend.
   */
  quiet: boolean
}

export const QUIET_AFTER_MS = 5000
/** Reconnect delays after the browser gives up on a stream (e.g. the backend was restarted). */
const RETRY_MS = [1000, 2000, 5000, 10000]

/**
 * Subscribe to a session's progress events. The backend replays its event history on every
 * connect, so replays are dropped here by key; the reducer in ./state is idempotent as well.
 */
export function useSessionStream(sessionId: string | undefined): SessionStream {
  const [state, setState] = useState<{ id?: string; events: IrisEvent[]; status: StreamStatus }>({
    events: [],
    status: 'connecting',
  })
  const [quietFor, setQuietFor] = useState<string | null>(null)

  useEffect(() => {
    if (!sessionId) return
    const id = sessionId
    const seen = new Set<string>()
    let source: EventSource | undefined
    let quietTimer: number | undefined
    let retryTimer: number | undefined
    let retries = 0
    let closed = false

    const setStatus = (status: StreamStatus) =>
      setState((prev) => (prev.id === id && prev.status === status ? prev : { ...prev, id, status }))

    // Headers may only arrive with the first byte (dev proxy), so time silence from each attempt,
    // not from `open`.
    function armQuietTimer() {
      window.clearTimeout(quietTimer)
      quietTimer = window.setTimeout(() => setQuietFor(id), QUIET_AFTER_MS)
    }

    function onEvent(event: Event) {
      // Transport errors also arrive as "error" but carry no application payload.
      const message = decodeSessionEvent(event, id)
      if (!message) return
      retries = 0
      window.clearTimeout(quietTimer)
      setQuietFor((q) => (q === id ? null : q))
      const key = eventKey(message)
      if (seen.has(key)) return setStatus('open')
      seen.add(key)
      setState((prev) => ({
        id,
        status: 'open',
        events: prev.id === id ? [...prev.events, message] : [message],
      }))
    }

    function connect() {
      if (closed) return
      const es = new EventSource(`/api/sessions/${id}/events`)
      source = es
      armQuietTimer()
      es.onopen = () => setStatus('open')
      es.onerror = (event) => {
        // Server "error" events arrive here too, as MessageEvents; only transport errors count.
        if (event instanceof MessageEvent) return
        setStatus('reconnecting')
        if (es.readyState === EventSource.CLOSED) {
          // The browser won't retry a failed response; do it ourselves with backoff.
          es.close()
          retryTimer = window.setTimeout(connect, RETRY_MS[Math.min(retries++, RETRY_MS.length - 1)])
        } else {
          armQuietTimer() // the browser is retrying; time the next attempt's silence
        }
      }
      EVENT_TYPES.forEach((t) => es.addEventListener(t, onEvent))
    }

    connect()
    return () => {
      closed = true
      window.clearTimeout(quietTimer)
      window.clearTimeout(retryTimer)
      source?.close()
    }
  }, [sessionId])

  if (state.id !== sessionId) return { events: [], status: 'connecting', quiet: false }
  return { events: state.events, status: state.status, quiet: quietFor === sessionId }
}
