const { checkApiKey } = require('./keystore.cjs')

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
