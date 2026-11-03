// Ending a live session: the backend only ends it once every audio chunk has arrived, so the
// browser sends how many it recorded. See backend api/routes/live.py.

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

export interface EndRequest {
  /** Audio chunks recorded (seq 0 to n-1). */
  audioChunks: number
  /** End even though some audio chunks were rejected; the backend fills them with silence. */
  allowGaps: boolean
}

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
    throw new EndSessionError('Could not reach Iris to finish the session. Is the backend running?')
  }
  if (res.ok) return
  if (res.status === 409) {
    const detail = ((await res.json().catch(() => null)) as { detail?: unknown } | null)?.detail
    if (detail && typeof detail === 'object' && 'missing_audio_chunks' in detail) {
      const { message, missing_audio_chunks: missing } = detail as {
        message?: string
        missing_audio_chunks?: number[]
      }
      throw new MissingAudioError(
        `Iris has not received all the audio yet (${message ?? 'audio chunks are missing'}).`,
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
  throw new EndSessionError(`Iris could not finish the session (HTTP ${res.status}).`)
}
