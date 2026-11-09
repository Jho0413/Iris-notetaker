// Typed API client. Types in schema.d.ts are generated from the backend's OpenAPI schema:
//   npm run gen:api   (with the backend running)
import createClient from 'openapi-fetch'
import { engineAnswersOnly } from '../session/request.ts'
import type { components, paths } from './schema'

export const api = createClient<paths>({ baseUrl: '/' })
api.use(engineAnswersOnly)

export type Session = components['schemas']['Session']
export type SessionStatus = components['schemas']['SessionStatus']
export type Stage = components['schemas']['Stage']
export type Notes = components['schemas']['Notes']
export type Transcript = components['schemas']['Transcript']
export type IrisEvent = components['schemas']['Event']
export type SessionUsage = components['schemas']['SessionUsageView']
export type AppSettings = components['schemas']['AppSettings']

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
