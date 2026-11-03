import type { Notes } from '../api/client'

/** Matches the backend's NotesDraft; replace with the generated type once the endpoint exists. */
export type NotesDraft = Pick<Notes, 'session_type' | 'title' | 'markdown'>

/**
 * The draft notes endpoint (`GET /api/sessions/{id}/notes/draft`) is planned with live processing.
 * Until it exists, the session page shows draft progress ("2 of 4 sections") without the text.
 * Turn this on once the endpoint is merged and `schema.d.ts` is regenerated.
 */
export const DRAFT_ENDPOINT_ENABLED = false

/** The current draft, or null when it isn't available (yet). */
export async function fetchDraft(sessionId: string): Promise<NotesDraft | null> {
  if (!DRAFT_ENDPOINT_ENABLED) return null
  try {
    const res = await fetch(`/api/sessions/${sessionId}/notes/draft`)
    return res.ok ? ((await res.json()) as NotesDraft) : null
  } catch {
    return null
  }
}
