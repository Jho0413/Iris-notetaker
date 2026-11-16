import type { Middleware } from 'openapi-fetch'

/**
 * Whether a response came from in front of the engine because the engine didn't answer: Vite's
 * proxy in development (502), or the desktop app's handler when the engine isn't running (503) or
 * doesn't answer (502). The engine itself never sends these, so they mean the same as no answer.
 */
export function engineDidNotAnswer(status: number): boolean {
  return status === 502 || status === 503
}

/**
 * Typed-client middleware that fails such a response the way a network failure fails, so every
 * page already handling "nothing answered" says Iris isn't responding instead of its own text.
 */
export const engineAnswersOnly: Middleware = {
  onResponse({ request, response }) {
    if (engineDidNotAnswer(response.status)) {
      throw new Error(`The engine didn't answer ${request.method} ${request.url} (${response.status})`)
    }
  },
}

/**
 * Run an openapi-fetch call for a user action, turning a call that rejects instead of returning an
 * error (a network failure, or the engine not answering) into a result, so the caller always gets
 * to reset its busy state.
 */
export async function act<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<{ ok: boolean; status: number; data?: T; error?: unknown }> {
  try {
    const { data, error, response } = await call
    return { ok: !error, status: response.status, data, error }
  } catch {
    return { ok: false, status: 0 }
  }
}
