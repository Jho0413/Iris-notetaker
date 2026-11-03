// Connection logic for a session's progress stream, kept free of React so it can be tested under
// plain Node with a fake EventSource (tests/stream.test.ts). useSessionStream wraps it.
import type { IrisEvent } from '../api/client'
import { decodeSessionEvent, EVENT_TYPES } from '../api/eventMessage.ts'
import { eventKey } from './state.ts'

export type StreamStatus = 'connecting' | 'open' | 'reconnecting'

/** The parts of EventSource the controller uses. */
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
  /** New (not replayed) events, batched so a replayed history renders once, not per event. */
  onEvents: (events: IrisEvent[]) => void
  onStatus: (status: StreamStatus) => void
  /** Whether the latest connection attempt has stayed silent for QUIET_AFTER_MS. */
  onQuiet: (quiet: boolean) => void
}

/** A running session always has history to replay, so this much silence means no active run. */
export const QUIET_AFTER_MS = 5000
/** Reconnect delays once the browser gives up on a stream (e.g. while the backend restarts). */
export const RETRY_MS = [1000, 2000, 5000, 10000]
/** How long events are collected before they are handed over together. */
export const BATCH_MS = 50
const CLOSED = 2

/**
 * Connect to a session's event stream. Replays (the backend resends its history on every
 * connect) are dropped by key. Returns a function that closes the stream.
 */
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

  // Headers may only arrive with the first byte (the dev proxy holds them), so silence is timed
  // from each connection attempt rather than from `open`.
  function armQuietTimer() {
    deps.clearTimeout(quietTimer)
    quietTimer = deps.setTimeout(() => callbacks.onQuiet(true), QUIET_AFTER_MS)
  }

  function onEvent(event: Event) {
    // Transport errors also arrive as "error" but carry no application payload.
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
      // Server "error" events arrive here too, as MessageEvents; only transport errors count.
      if (event instanceof MessageEvent) return
      callbacks.onStatus('reconnecting')
      if (es.readyState === CLOSED) {
        // The browser won't retry a failed response; do it ourselves with backoff.
        es.close()
        retryTimer = deps.setTimeout(connect, RETRY_MS[Math.min(retries++, RETRY_MS.length - 1)])
      } else {
        armQuietTimer() // the browser is retrying; time the next attempt's silence
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
