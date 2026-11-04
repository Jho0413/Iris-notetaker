import type { NotesDraft, SessionStatus } from '../api/client'
import type { RunState } from './state'

export type { NotesDraft }

/**
 * The notes as far as they're written (`GET /api/sessions/{id}/notes/draft`): the live draft
 * while recording, then the write stage's sections while processing. Figure markers are resolved
 * by the backend, like the final notes: to the redrawn figure once it exists, to the original crop
 * until then.
 *
 * Null when there is no draft: none yet, or the backend dropped it because the run is selecting
 * figures again, so its figure ids no longer apply. Callers clear what they show. Undefined when
 * the answer is unknown (backend unreachable, an error, an unreadable body). Callers keep what
 * they show.
 */
export async function fetchDraft(
  sessionId: string,
  fetchFn: typeof fetch = (input, init) => fetch(input, init),
): Promise<NotesDraft | null | undefined> {
  try {
    const res = await fetchFn(`/api/sessions/${encodeURIComponent(sessionId)}/notes/draft`)
    if (res.status === 404) return null
    return res.ok ? ((await res.json()) as NotesDraft) : undefined
  } catch {
    return undefined
  }
}

/**
 * When to (re)fetch the draft: a key that changes with every `notes_updated` event while the
 * session is recording or processing, and is null when no draft is wanted (other states, or once
 * the final notes are out). Pages fetch once on load (`0`) and again whenever it changes.
 */
export function draftRefreshKey(status: SessionStatus | undefined, run: RunState): number | null {
  if (status !== 'recording' && status !== 'processing') return null
  if (run.notes?.final) return null
  return run.notes?.updatedAt ?? 0
}
