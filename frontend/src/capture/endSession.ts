import { engineDidNotAnswer } from '../api/request.ts'

type Fetch = (input: string, init?: RequestInit) => Promise<Response>

export interface EndRequest {
  audioChunks: number
  allowGaps: boolean
}

const UNREACHABLE = "Iris isn't responding, so the session couldn't be finished. Try again in a moment."

export class EndSessionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'EndSessionError'
  }
}

export class MissingAudioError extends EndSessionError {
  readonly missing: number[]
  constructor(message: string, missing: number[]) {
    super(message)
    this.name = 'MissingAudioError'
    this.missing = missing
  }
}

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
    const session = await fetchFn(`/api/sessions/${sessionId}`).catch(() => null)
    if (session?.ok) {
      const { status } = (await session.json()) as { status?: string }
      if (status && status !== 'recording') return
    }
  }
  throw new EndSessionError("Iris couldn't finish the session. Try again in a moment.")
}

export async function stopLiveSession(sessionId: string, fetchFn: Fetch = fetch): Promise<void> {
  const notStopped = "Iris isn't responding, so the recording couldn't be stopped. Try again in a moment."
  let res: Response
  try {
    res = await fetchFn(`/api/live/${sessionId}/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    })
  } catch {
    throw new EndSessionError(notStopped)
  }
  if (engineDidNotAnswer(res.status)) throw new EndSessionError(notStopped)
  if (res.ok || res.status === 409 || res.status === 404) return
  throw new EndSessionError("Iris couldn't stop the recording. Try again in a moment.")
}
