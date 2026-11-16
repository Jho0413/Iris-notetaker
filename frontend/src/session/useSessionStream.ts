import { useEffect, useState } from 'react'
import { applyEvent, emptyRun, type RunState } from './state'
import { connectSessionStream, type StreamStatus } from './stream'

export type { StreamStatus } from './stream'

export interface SessionStream {
  run: RunState
  eventCount: number
  status: StreamStatus
  quiet: boolean
}

interface State extends SessionStream {
  id?: string
}

const initial: State = { run: emptyRun(), eventCount: 0, status: 'connecting', quiet: false }

export function useSessionStream(sessionId: string | undefined): SessionStream {
  const [state, setState] = useState<State>(initial)

  useEffect(() => {
    if (!sessionId) return
    const id = sessionId
    const forThis = (prev: State): State => (prev.id === id ? prev : { ...initial, id })
    return connectSessionStream(
      id,
      {
        onEvents: (events) =>
          setState((prev) => {
            const base = forThis(prev)
            return {
              ...base,
              run: events.reduce(applyEvent, base.run),
              eventCount: base.eventCount + events.length,
            }
          }),
        onStatus: (status) =>
          setState((prev) => {
            const base = forThis(prev)
            return base.status === status && base === prev ? prev : { ...base, status }
          }),
        onQuiet: (quiet) =>
          setState((prev) => {
            const base = forThis(prev)
            return base.quiet === quiet && base === prev ? prev : { ...base, quiet }
          }),
      },
      {
        createSource: (url) => new EventSource(url),
        setTimeout: (fn, ms) => window.setTimeout(fn, ms),
        clearTimeout: (t) => window.clearTimeout(t),
      },
    )
  }, [sessionId])

  return state.id === sessionId ? state : initial
}
