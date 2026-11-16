// Saving a new key from the key window, in order (tested in test/keysave.test.cjs):
// its format, then one check with OpenAI, then whether the window is still open, then store.
// The check can take seconds; a window closed meanwhile means the user cancelled, so nothing is
// stored. The result never contains the key.

const { checkApiKey } = require('./keystore.cjs')

/**
 * @param {unknown} input  what was typed
 * @param {object} deps
 * @param {(key: string) => Promise<{ok: boolean, error?: string, reason?: string}>} deps.check
 * @param {() => boolean} deps.stillOpen  the key window that asked is still the open one
 * @param {(key: string) => string} deps.store  encrypt and store; returns the stored key
 * @returns {Promise<{ok: true, key: string} | {ok: false, error: string, reason?: string, cancelled?: true}>}
 *   `key` is for the main process only, never for the page.
 */
async function saveNewKey(input, { check, stillOpen, store }) {
  const checked = checkApiKey(input)
  if (!checked.ok) return { ok: false, error: checked.error }
  const result = await check(checked.key)
  if (!result.ok) return { ok: false, error: result.error ?? '', reason: result.reason }
  if (!stillOpen()) return { ok: false, error: '', cancelled: true }
  try {
    store(checked.key)
    return { ok: true }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}

module.exports = { saveNewKey }
