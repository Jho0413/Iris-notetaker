// Upload queue for live capture: audio chunks and frames are posted with bounded concurrency,
// and failed posts are retried with backoff until they succeed or are rejected, so a dropped
// request or a backend restart doesn't lose content. The backend stores each chunk by its key
// (audio seq, frame time), so a retry of a post that actually landed is harmless.

export type UploadKind = 'audio' | 'frame'

/** Posts one item; resolves with the HTTP status, rejects on a network error. */
export type Send = (path: string, body: Blob) => Promise<number>

export interface RejectedUpload {
  kind: UploadKind
  path: string
  status: number
}

export interface UploadQueueOptions {
  concurrency?: number
  baseDelayMs?: number
  maxDelayMs?: number
  /** Most frames held while uploads are failing; the oldest are dropped beyond this. */
  maxQueuedFrames?: number
}

interface Item {
  kind: UploadKind
  path: string
  body: Blob
  attempts: number
}

/** Statuses worth retrying: timeouts, rate limits and server errors. Other 4xx are final. */
export function isRetryable(status: number): boolean {
  return status === 408 || status === 429 || status >= 500
}

export class UploadQueue {
  readonly rejected: RejectedUpload[] = []
  droppedFrames = 0
  private waiting: Item[] = []
  private active = 0
  private retrying = 0
  private listeners = new Set<() => void>()
  private closed = false
  private readonly send: Send
  private readonly concurrency: number
  private readonly baseDelayMs: number
  private readonly maxDelayMs: number
  private readonly maxQueuedFrames: number

  constructor(send: Send, options: UploadQueueOptions = {}) {
    this.send = send
    this.concurrency = options.concurrency ?? 4
    this.baseDelayMs = options.baseDelayMs ?? 500
    this.maxDelayMs = options.maxDelayMs ?? 10_000
    this.maxQueuedFrames = options.maxQueuedFrames ?? 120
  }

  /** Items not yet uploaded: waiting, in flight or waiting to retry. */
  get pending(): number {
    return this.waiting.length + this.active + this.retrying
  }

  /** Whether some uploads have failed at least once and are being retried. */
  get struggling(): boolean {
    return this.retrying > 0 || this.waiting.some((i) => i.attempts > 0)
  }

  add(kind: UploadKind, path: string, body: Blob): void {
    if (this.closed) return
    this.waiting.push({ kind, path, body, attempts: 0 })
    if (kind === 'frame') this.dropOldFrames()
    this.pump()
  }

  /**
   * Wait until everything is uploaded (or rejected), or until `timeoutMs` passes. Returns how
   * many items are still pending; they stay queued, so calling drain again keeps retrying.
   */
  drain(timeoutMs: number): Promise<number> {
    if (this.pending === 0) return Promise.resolve(0)
    return new Promise((resolve) => {
      const done = () => {
        if (this.pending > 0) return
        clearTimeout(timer)
        this.listeners.delete(done)
        resolve(0)
      }
      const timer = setTimeout(() => {
        this.listeners.delete(done)
        resolve(this.pending)
      }, timeoutMs)
      this.listeners.add(done)
    })
  }

  /** Send nothing more: what is waiting or waiting to retry is dropped (the session is gone). */
  close(): void {
    this.closed = true
    this.waiting = []
  }

  private pump(): void {
    while (this.active < this.concurrency && this.waiting.length) {
      void this.upload(this.waiting.shift()!)
    }
  }

  private async upload(item: Item): Promise<void> {
    this.active++
    let status: number | null
    try {
      status = await this.send(item.path, item.body)
    } catch {
      status = null // network error: the backend is down or the request was dropped
    }
    this.active--
    if (status !== null && status >= 200 && status < 300) {
      // uploaded
    } else if (status !== null && !isRetryable(status)) {
      this.rejected.push({ kind: item.kind, path: item.path, status })
    } else {
      item.attempts++
      this.retrying++
      const delay = Math.min(this.maxDelayMs, this.baseDelayMs * 2 ** (item.attempts - 1))
      setTimeout(() => {
        this.retrying--
        if (this.closed) return
        this.waiting.push(item)
        if (item.kind === 'frame') this.dropOldFrames()
        this.pump()
      }, delay)
    }
    this.pump()
    this.listeners.forEach((listener) => listener())
  }

  /** Bound memory while the backend is unreachable. Audio is never dropped. */
  private dropOldFrames(): void {
    let frames = this.waiting.filter((i) => i.kind === 'frame').length
    while (frames > this.maxQueuedFrames) {
      const oldest = this.waiting.findIndex((i) => i.kind === 'frame')
      this.waiting.splice(oldest, 1)
      this.droppedFrames++
      frames--
    }
  }
}
