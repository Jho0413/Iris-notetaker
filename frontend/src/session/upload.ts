import type { Session } from '../api/client'
import { engineDidNotAnswer } from '../api/request.ts'

/** Why an upload failed. Its message is ours: never the backend's detail or the status code. */
export class UploadError extends Error {
  readonly kind: 'network' | 'rejected' | 'aborted'
  constructor(kind: 'network' | 'rejected' | 'aborted') {
    super(kind)
    this.kind = kind
  }
}

/**
 * Upload a recording with byte progress: a multipart POST to /api/sessions, over XMLHttpRequest
 * because fetch can't report upload progress and hour-long recordings are large.
 */
export function uploadWithProgress(
  file: File,
  title: string | undefined,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
  createXhr: () => XMLHttpRequest = () => new XMLHttpRequest(),
): Promise<Session> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    const xhr = createXhr()
    xhr.open('POST', '/api/sessions')
    xhr.responseType = 'json'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    const lost = () => reject(new UploadError('network'))
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response) return resolve(xhr.response)
      if (engineDidNotAnswer(xhr.status)) return lost()
      reject(new UploadError('rejected'))
    }
    xhr.onerror = lost
    xhr.onabort = () => reject(new UploadError('aborted'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(form)
  })
}
