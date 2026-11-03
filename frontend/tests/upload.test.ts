import assert from 'node:assert/strict'
import test from 'node:test'
import { UploadError, uploadWithProgress } from '../src/session/upload.ts'

/** A stand-in XMLHttpRequest the test drives by hand. */
class FakeXhr {
  status = 0
  response: unknown = null
  responseType = ''
  sent: FormData | null = null
  url = ''
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  open(_method: string, url: string) {
    this.url = url
  }
  send(body: FormData) {
    this.sent = body
  }
  abort() {
    this.onabort?.()
  }
}

function start(title?: string, signal?: AbortSignal) {
  const xhr = new FakeXhr()
  const progress: number[] = []
  const file = new File(['abc'], 'lecture.mp4', { type: 'video/mp4' })
  const result = uploadWithProgress(file, title, (f) => progress.push(f), signal,
    () => xhr as unknown as XMLHttpRequest)
  return { xhr, progress, result }
}

test('reports progress and resolves with the created session', async () => {
  const { xhr, progress, result } = start('Algebra')
  assert.equal(xhr.url, '/api/sessions')
  assert.equal(xhr.sent?.get('title'), 'Algebra')
  assert.equal((xhr.sent!.get('file') as File).name, 'lecture.mp4')
  xhr.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 4 })
  xhr.upload.onprogress?.({ lengthComputable: false, loaded: 2, total: 0 })
  xhr.status = 201
  xhr.response = { id: 's1' }
  xhr.onload?.()
  assert.deepEqual(await result, { id: 's1' })
  assert.deepEqual(progress, [0.25])
})

test('no title is sent when none is given', async () => {
  const { xhr, result } = start(undefined)
  assert.equal(xhr.sent?.has('title'), false)
  xhr.status = 201
  xhr.response = { id: 's1' }
  xhr.onload?.()
  await result
})

test('a rejection carries the backend detail', async () => {
  const { xhr, result } = start()
  xhr.status = 422
  xhr.response = { detail: 'unsupported file' }
  xhr.onload?.()
  await assert.rejects(result, (err) => err instanceof UploadError && err.kind === 'rejected' &&
    err.message === 'unsupported file')
})

test('a rejection without a detail reports the HTTP status', async () => {
  const { xhr, result } = start()
  xhr.status = 500
  xhr.onload?.()
  await assert.rejects(result, (err) => err instanceof UploadError && err.message === 'HTTP 500')
})

test('network failures and cancellation are told apart', async () => {
  const net = start()
  net.xhr.onerror?.()
  await assert.rejects(net.result, (err) => err instanceof UploadError && err.kind === 'network')

  const controller = new AbortController()
  const cancelled = start(undefined, controller.signal)
  controller.abort()
  await assert.rejects(cancelled.result, (err) => err instanceof UploadError && err.kind === 'aborted')
})
