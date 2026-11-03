/** Outcome of an API action: `status` is 0 when the request never reached the backend. */
export interface ActionResult {
  ok: boolean
  status: number
}

/**
 * Run an openapi-fetch call for a user action, turning a network failure (which rejects instead of
 * returning an error) into a result, so the caller always gets to reset its busy state.
 */
export async function act(
  call: Promise<{ error?: unknown; response: Response }>,
): Promise<ActionResult> {
  try {
    const { error, response } = await call
    return { ok: !error, status: response.status }
  } catch {
    return { ok: false, status: 0 }
  }
}
