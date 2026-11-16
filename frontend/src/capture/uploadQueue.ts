export type UploadKind = 'audio' | 'frame'

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
  maxQueuedFrames?: number
}

interface Item {
  kind: UploadKind
  path: string
  body: Blob
  attempts: number
}

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

  get pending(): number {
    return this.waiting.length + this.active + this.retrying
  }

  get struggling(): boolean {
    return this.retrying > 0 || this.waiting.some((i) => i.attempts > 0)
  }

  add(kind: UploadKind, path: string, body: Blob): void {
    if (this.closed) return
    this.waiting.push({ kind, path, body, attempts: 0 })
    if (kind === 'frame') this.dropOldFrames()
    this.pump()
  }

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
      status = null
    }
    this.active--
    if (status !== null && status >= 200 && status < 300) {
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
