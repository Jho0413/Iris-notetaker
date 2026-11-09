// Ending a live session: the browser sends how many audio chunks it recorded, and the backend
// refuses to end it while any is missing (409), unless `allow_gaps` is set, when a missing one
// becomes silence. Or stopping it, which keeps what arrived without making notes. See backend
// api/routes/live.py.

import { engineDidNotAnswer } from '../session/request.ts'

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

export interface EndRequest {
  /** Audio chunks recorded (seq 0 to n-1). */
  audioChunks: number
  /** End even though some audio chunks were rejected; the backend fills them with silence. */
  allowGaps: boolean
}

// Messages are ours, written for the user: never the backend's text or status codes.
const UNREACHABLE = "Iris isn't responding, so the session couldn't be finished. Try again in a moment."

/** A failure to finish the session, with a message that can be shown to the user as is. */
export class EndSessionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'EndSessionError'
  }
}

/** The backend has not received every audio chunk; `missing` lists (some of) their seqs. */
export class MissingAudioError extends EndSessionError {
  readonly missing: number[]
  constructor(message: string, missing: number[]) {
    super(message)
    this.name = 'MissingAudioError'
    this.missing = missing
  }
}

/** Ask the backend to stop recording and process the session. Safe to call again on failure. */
export async function endLiveSession(
  sessionId: string,
  request: EndRequest,
  fetchFn: Fetch = fetch,
): Promise<void> {
  let res: Response
  try {
    res = await fetchFn(`/api/live/${sessionId}/end`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio_chunks: request.audioChunks, allow_gaps: request.allowGaps }),
    })
  } catch {
    throw new EndSessionError(UNREACHABLE)
  }
  if (res.ok) return
  if (engineDidNotAnswer(res.status)) throw new EndSessionError(UNREACHABLE)
  if (res.status === 409) {
    const detail = ((await res.json().catch(() => null)) as { detail?: unknown } | null)?.detail
    if (detail && typeof detail === 'object' && 'missing_audio_chunks' in detail) {
      const { missing_audio_chunks: missing } = detail as { missing_audio_chunks?: number[] }
      throw new MissingAudioError(
        "Iris hasn't received all the audio yet. Try again in a moment.",
        Array.isArray(missing) ? missing : [],
      )
    }
    // Not recording: an earlier end request may have succeeded without its response arriving.
    const session = await fetchFn(`/api/sessions/${sessionId}`).catch(() => null)
    if (session?.ok) {
      const { status } = (await session.json()) as { status?: string }
      if (status && status !== 'recording') return
    }
  }
  throw new EndSessionError("Iris couldn't finish the session. Try again in a moment.")
}

/**
 * Ask the backend to stop recording and keep what arrived, without making notes. Safe to call
 * again on failure: stopping a stopped session is a no-op.
 */
export async function stopLiveSession(sessionId: string, fetchFn: Fetch = fetch): Promise<void> {
  const notStopped = "Iris isn't responding, so the recording couldn't be stopped. Try again in a moment."
  let res: Response
  try {
    // A body although it says nothing: the desktop app can't forward a request with an empty one.
    res = await fetchFn(`/api/live/${sessionId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
  } catch {
    throw new EndSessionError(notStopped)
  }
  if (engineDidNotAnswer(res.status)) throw new EndSessionError(notStopped)
  // Nothing left to keep: 409, no longer recording (its notes are being made, or were); 404, the
  // session is gone (deleted elsewhere), so retrying could never succeed.
  if (res.ok || res.status === 409 || res.status === 404) return
  throw new EndSessionError("Iris couldn't stop the recording. Try again in a moment.")
}
