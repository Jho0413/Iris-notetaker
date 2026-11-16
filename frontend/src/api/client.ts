import createClient from 'openapi-fetch'
import { engineAnswersOnly } from './request.ts'
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

export function sessionFileUrl(sessionId: string, relPath: string): string {
  return `/api/sessions/${sessionId}/files/${relPath}`
}
