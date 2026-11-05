// Check a new OpenAI API key with OpenAI before it is saved (tested in test/keycheck.test.cjs).
//
// One request, made once, when the user saves a new key: listing the models, which is free and
// needs only a valid key. The key is sent only to OpenAI, never logged, and never part of a message.

const MODELS_URL = 'https://api.openai.com/v1/models'
const TIMEOUT_MS = 15_000

const REJECTED = 'OpenAI rejected this key. Check you copied all of it, or create a new one.'
const UNREACHABLE =
  'Iris couldn’t reach OpenAI to check the key. Check your connection and try again. The key wasn’t saved.'
const CANT_CHECK = 'OpenAI couldn’t check the key just now. Try again in a moment. The key wasn’t saved.'

/**
 * Ask OpenAI whether `key` works. Resolves {ok: true}, or {ok: false, error} to show in the key
 * window. Never rejects.
 * @param {string} key  a key that already passed checkApiKey (keystore.cjs)
 * @param {{ fetch?: typeof fetch, timeoutMs?: number }} [opts]
 */
async function checkKeyWithOpenAI(key, { fetch = globalThis.fetch, timeoutMs = TIMEOUT_MS } = {}) {
  let response
  try {
    response = await fetch(MODELS_URL, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(timeoutMs),
    })
  } catch (err) {
    // offline, DNS, TLS or the timeout. The reason (an error code or name, never the key) is for
    // the log, so a proxy or network problem can be told apart.
    return { ok: false, error: UNREACHABLE, reason: errorReason(err) }
  }
  await response.body?.cancel().catch(() => {}) // the model list itself isn't needed
  if (response.ok) return { ok: true }
  // 401: wrong or revoked key. 403: the key may not use the API (e.g. a restricted project).
  const reason = `status ${response.status}`
  if (response.status === 401 || response.status === 403) return { ok: false, error: REJECTED, reason }
  return { ok: false, error: CANT_CHECK, reason } // rate limits and OpenAI's own errors: the key may be fine
}

/** A short, key-free cause of a failed request: `ENOTFOUND`, `UNABLE_TO_VERIFY_LEAF_SIGNATURE`, `TimeoutError`. */
function errorReason(err) {
  // Text codes only: a DOMException (e.g. the timeout) also has a legacy numeric `code`.
  const code = [err?.cause?.code, err?.code].find((c) => typeof c === 'string')
  return code ?? String(err?.name ?? 'unknown error')
}

module.exports = { checkKeyWithOpenAI, MODELS_URL, REJECTED, UNREACHABLE, CANT_CHECK }
