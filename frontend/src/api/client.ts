// Typed API client. Types in schema.d.ts are generated from the backend's OpenAPI schema:
//   npm run gen:api   (with the backend running)
import createClient from 'openapi-fetch'
import type { components, paths } from './schema'

export const api = createClient<paths>({ baseUrl: '/' })

export type Session = components['schemas']['Session']
export type SessionStatus = components['schemas']['SessionStatus']
export type Stage = components['schemas']['Stage']
export type Notes = components['schemas']['Notes']
export type Transcript = components['schemas']['Transcript']
export type IrisEvent = components['schemas']['Event']
export type SessionUsage = components['schemas']['SessionUsage']

export const STAGES: Stage[] = [
  'ingest',
  'transcribe',
  'detect',
  'align',
  'select',
  'write',
  'render',
  'assemble',
  'synthesize',
]

/** URL for a file inside a session folder, e.g. "visuals/v1.jpg". */
export function sessionFileUrl(sessionId: string, relPath: string): string {
  return `/api/sessions/${sessionId}/files/${relPath}`
}

/** Multipart upload; openapi-fetch needs the body serialised by hand for FormData. */
export async function uploadRecording(file: File, title?: string): Promise<Session> {
  const form = new FormData()
  form.append('file', file)
  if (title) form.append('title', title)
  const res = await fetch('/api/sessions', { method: 'POST', body: form })
  if (!res.ok) throw new Error(`Upload failed: ${res.status} ${await res.text()}`)
  return res.json()
}
