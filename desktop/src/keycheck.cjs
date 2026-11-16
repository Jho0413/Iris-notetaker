const MODELS_URL = 'https://api.openai.com/v1/models'
const TIMEOUT_MS = 15_000

const REJECTED = 'OpenAI rejected this key. Check you copied all of it, or create a new one.'
const UNREACHABLE =
  'Iris couldn’t reach OpenAI to check the key. Check your connection and try again. The key wasn’t saved.'
const CANT_CHECK = 'OpenAI couldn’t check the key just now. Try again in a moment. The key wasn’t saved.'

async function checkKeyWithOpenAI(key, { fetch = globalThis.fetch, timeoutMs = TIMEOUT_MS } = {}) {
  let response
  try {
    response = await fetch(MODELS_URL, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (err) {
    return { ok: false, error: UNREACHABLE, reason: errorReason(err) }
  }
  await response.body?.cancel().catch(() => {})
  if (response.ok) return { ok: true }
  const reason = `status ${response.status}`
  if (response.status === 401 || response.status === 403) return { ok: false, error: REJECTED, reason }
  return { ok: false, error: CANT_CHECK, reason }
}

function errorReason(err) {
  const code = [err?.cause?.code, err?.code].find((c) => typeof c === 'string')
  return code ?? String(err?.name ?? 'unknown error')
}

module.exports = { checkKeyWithOpenAI, MODELS_URL, REJECTED, UNREACHABLE, CANT_CHECK }
