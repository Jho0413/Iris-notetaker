const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')
const { checkApiKey, createKeyStore } = require('../src/keystore.cjs')

const KEY = 'sk-proj-' + 'a'.repeat(40)

function fakeSafeStorage({ available = true } = {}) {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (s) => Buffer.from('enc:' + Buffer.from(s).toString('base64')),
    decryptString: (b) => {
      const s = b.toString()
      if (!s.startsWith('enc:')) throw new Error('bad data')
      return Buffer.from(s.slice(4), 'base64').toString()
    },
  }
}

function tmpFile() {
  return path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'iris-key-')), 'nested', 'openai-key.bin')
}

test('checkApiKey', () => {
  assert.deepEqual(checkApiKey(`  ${KEY}\n`), { ok: true, key: KEY })
  for (const bad of ['', '   ', null, 42, 'pk-' + 'a'.repeat(40), 'sk-short', `sk-${'a'.repeat(20)} ${'b'.repeat(20)}`, `sk-${'a'.repeat(30)}é`]) {
    const r = checkApiKey(bad)
    assert.equal(r.ok, false, String(bad))
    assert.ok(r.error.length > 0)
  }
})

test('save encrypts, load decrypts, file is private, and load follows save and clear', () => {
  const file = tmpFile()
  const store = createKeyStore({ safeStorage: fakeSafeStorage(), file })
  assert.equal(store.has(), false)
  assert.equal(store.load(), null)
  store.save(`${KEY}\n`)
  assert.equal(store.has(), true)
  assert.equal(store.load(), KEY)
  assert.equal(createKeyStore({ safeStorage: fakeSafeStorage(), file }).load(), KEY)
  assert.ok(!fs.readFileSync(file).toString().includes(KEY), 'the key is never on disk in plain text')
  assert.equal(fs.statSync(file).mode & 0o777, 0o600)
  assert.ok(!fs.existsSync(file + '.tmp'))
  store.clear()
  assert.equal(store.has(), false)
  assert.equal(store.load(), null)
})

test('save refuses bad keys and missing encryption, storing nothing', () => {
  const file = tmpFile()
  assert.throws(() => createKeyStore({ safeStorage: fakeSafeStorage(), file }).save('nope'), /start with "sk-"/)
  assert.throws(() => createKeyStore({ safeStorage: fakeSafeStorage({ available: false }), file }).save(KEY), /Keychain/)
  assert.equal(fs.existsSync(file), false)
})

test('a key that cannot be decrypted loads as null (ask again)', () => {
  const file = tmpFile()
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, 'garbage')
  const store = createKeyStore({ safeStorage: fakeSafeStorage(), file })
  assert.equal(store.has(), true)
  assert.equal(store.load(), null)
})

test('load reads the key once, a failed read too', () => {
  const file = tmpFile()
  createKeyStore({ safeStorage: fakeSafeStorage(), file }).save(KEY)
  const store = createKeyStore({ safeStorage: fakeSafeStorage(), file })
  assert.equal(store.load(), KEY)
  fs.writeFileSync(file, 'garbage')
  assert.equal(store.load(), KEY)
  const unreadable = createKeyStore({ safeStorage: fakeSafeStorage(), file })
  assert.equal(unreadable.load(), null)
  store.save(KEY)
  assert.equal(unreadable.load(), null)
})
