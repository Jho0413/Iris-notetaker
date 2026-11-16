import type { Middleware } from 'openapi-fetch'

export function engineDidNotAnswer(status: number): boolean {
  return status === 502 || status === 503
}

export const engineAnswersOnly: Middleware = {
  onResponse({ request, response }) {
    if (engineDidNotAnswer(response.status)) {
      throw new Error(`The engine didn't answer ${request.method} ${request.url} (${response.status})`)
    }
  },
}

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
