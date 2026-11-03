import type { Session } from '../api/client'

export class UploadError extends Error {
  readonly kind: 'network' | 'rejected' | 'aborted'
  constructor(kind: 'network' | 'rejected' | 'aborted', message: string) {
    super(message)
    this.kind = kind
  }
}

/**
 * Upload a recording with byte progress. Same request as `uploadRecording` in api/client.ts, over
 * XMLHttpRequest because fetch can't report upload progress and hour-long recordings are large.
 */
export function uploadWithProgress(
  file: File,
  title: string | undefined,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<Session> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/sessions')
    xhr.responseType = 'json'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300 && xhr.response) return resolve(xhr.response)
      const detail = (xhr.response as { detail?: unknown } | null)?.detail
      reject(new UploadError('rejected', typeof detail === 'string' ? detail : `HTTP ${xhr.status}`))
    }
    xhr.onerror = () => reject(new UploadError('network', 'The connection to the backend was lost'))
    xhr.onabort = () => reject(new UploadError('aborted', 'Upload cancelled'))
    signal?.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(form)
  })
}
