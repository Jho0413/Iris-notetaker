const assert = require('node:assert/strict')
const { test } = require('node:test')
const { saveNewKey } = require('../src/keysave.cjs')

const KEY = 'sk-test-0123456789abcdefghij'

/** Fakes that record what happened, in order. */
function deps({ check = { ok: true }, open = true, storeThrows = null } = {}) {
  const log = []
  return {
    log,
    check: async (key) => (log.push(['check', key]), typeof check === 'function' ? check() : check),
    stillOpen: () => (log.push(['stillOpen']), typeof open === 'function' ? open() : open),
    store: (key) => {
      log.push(['store', key])
      if (storeThrows) throw new Error(storeThrows)
      return key
    },
  }
}

test('a good key is checked, then stored', async () => {
  const d = deps()
  assert.deepEqual(await saveNewKey(`  ${KEY}\n`, d), { ok: true, key: KEY })
  assert.deepEqual(d.log, [['check', KEY], ['stillOpen'], ['store', KEY]])
})

test('a badly formatted key is refused before anything is sent to OpenAI', async () => {
  const d = deps()
  const result = await saveNewKey('not-a-key', d)
  assert.equal(result.ok, false)
  assert.match(result.error, /start with "sk-"/)
  assert.deepEqual(d.log, [])
})

test('a key OpenAI turns down is never stored, and says why', async () => {
  const d = deps({ check: { ok: false, error: 'OpenAI rejected this key.', reason: 'status 401' } })
  assert.deepEqual(await saveNewKey(KEY, d), { ok: false, error: 'OpenAI rejected this key.', reason: 'status 401' })
  assert.deepEqual(d.log, [['check', KEY]])
})

test('closing the window while the key is checked stores nothing', async () => {
  let open = true
  const d = deps({ check: () => ((open = false), { ok: true }), open: () => open })
  assert.deepEqual(await saveNewKey(KEY, d), { ok: false, error: '', cancelled: true })
  assert.ok(!d.log.some(([what]) => what === 'store'))
})

test('a storage failure is reported, not thrown', async () => {
  const d = deps({ storeThrows: 'macOS Keychain encryption is not available.' })
  assert.deepEqual(await saveNewKey(KEY, d), { ok: false, error: 'macOS Keychain encryption is not available.' })
})

test('no failed result carries the key', async () => {
  const outcomes = [
    deps({ check: { ok: false, error: 'x', reason: 'ENOTFOUND' } }),
    deps({ open: false }),
    deps({ storeThrows: 'y' }),
  ]
  for (const d of outcomes) assert.ok(!JSON.stringify(await saveNewKey(KEY, d)).includes(KEY))
})
