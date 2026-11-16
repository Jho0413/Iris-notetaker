import type { IrisEvent } from '../api/client'
import { decodeSessionEvent, EVENT_TYPES } from '../api/eventMessage.ts'
import { eventKey } from './state.ts'

export type StreamStatus = 'connecting' | 'open' | 'reconnecting'

export interface EventSourceLike extends EventTarget {
  readonly readyState: number
  onopen: ((event: Event) => void) | null
  onerror: ((event: Event) => void) | null
  close(): void
}

export interface StreamDeps {
  createSource: (url: string) => EventSourceLike
  setTimeout: (fn: () => void, ms: number) => number
  clearTimeout: (id: number | undefined) => void
}

export interface StreamCallbacks {
  onEvents: (events: IrisEvent[]) => void
  onStatus: (status: StreamStatus) => void
  onQuiet: (quiet: boolean) => void
}

export const QUIET_AFTER_MS = 5000
export const RETRY_MS = [1000, 2000, 5000, 10000]
export const BATCH_MS = 50
const CLOSED = 2

export function connectSessionStream(
  sessionId: string,
  callbacks: StreamCallbacks,
  deps: StreamDeps,
): () => void {
  const seen = new Set<string>()
  let pending: IrisEvent[] = []
  let source: EventSourceLike | undefined
  let quietTimer: number | undefined
  let retryTimer: number | undefined
  let flushTimer: number | undefined
  let retries = 0
  let closed = false

  function flush() {
    flushTimer = undefined
    if (closed || !pending.length) return
    const batch = pending
    pending = []
    callbacks.onEvents(batch)
  }

  function armQuietTimer() {
    deps.clearTimeout(quietTimer)
    quietTimer = deps.setTimeout(() => callbacks.onQuiet(true), QUIET_AFTER_MS)
  }

  function onEvent(event: Event) {
    const message = decodeSessionEvent(event, sessionId)
    if (!message) return
    retries = 0
    deps.clearTimeout(quietTimer)
    callbacks.onQuiet(false)
    callbacks.onStatus('open')
    const key = eventKey(message)
    if (seen.has(key)) return
    seen.add(key)
    pending.push(message)
    if (flushTimer === undefined) flushTimer = deps.setTimeout(flush, BATCH_MS)
  }

  function connect() {
    if (closed) return
    const es = deps.createSource(`/api/sessions/${sessionId}/events`)
    source = es
    armQuietTimer()
    es.onopen = () => callbacks.onStatus('open')
    es.onerror = (event) => {
      if (event instanceof MessageEvent) return
      callbacks.onStatus('reconnecting')
      if (es.readyState === CLOSED) {
        es.close()
        retryTimer = deps.setTimeout(connect, RETRY_MS[Math.min(retries++, RETRY_MS.length - 1)])
      } else {
        armQuietTimer()
      }
    }
    EVENT_TYPES.forEach((t) => es.addEventListener(t, onEvent))
  }

  connect()
  return () => {
    closed = true
    deps.clearTimeout(quietTimer)
    deps.clearTimeout(retryTimer)
    deps.clearTimeout(flushTimer)
    source?.close()
  }
}
