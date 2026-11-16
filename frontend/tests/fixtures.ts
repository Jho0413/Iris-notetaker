import type { IrisEvent, Session } from '../src/api/client.ts'

let tick = 0
export function ev(type: IrisEvent['type'], data: Record<string, unknown> = {}, seconds?: number): IrisEvent {
  tick += 1
  return { type, session_id: 's1', data, at: new Date(Date.UTC(2026, 10, 3, 12, 0, seconds ?? tick)).toISOString() }
}

export const session: Session = {
  id: 's1', title: 'Algebra', source: 'upload', status: 'processing', current_stage: null,
  failure_message: null, failure_kind: null, duration_s: null, created_at: '2026-11-03T12:00:00Z', updated_at: '2026-11-03T12:00:00Z',
}
