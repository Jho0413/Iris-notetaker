// The OpenAI API key, encrypted with Electron's safeStorage (the macOS Keychain holds the
// encryption key) in a file in userData. Never written in plain text, never logged, never sent
// to a page: main.cjs passes it to the backend on its stdin at each start, never in its
// environment.

const fs = require('node:fs')
const path = require('node:path')

/** Trim and check a pasted key. Returns the key, or an error message for the key screen. */
function checkApiKey(input) {
  const key = typeof input === 'string' ? input.trim() : ''
  if (!key) return { ok: false, error: 'Paste your OpenAI API key.' }
  if (!key.startsWith('sk-')) return { ok: false, error: 'OpenAI API keys start with "sk-".' }
  if (/\s/.test(key)) return { ok: false, error: 'The key has a space or line break in it.' }
  if (!/^[\x21-\x7e]+$/.test(key)) return { ok: false, error: 'The key has characters an OpenAI key never has.' }
  if (key.length < 20 || key.length > 512) return { ok: false, error: "That doesn't look like a whole OpenAI API key." }
  return { ok: true, key }
}

/**
 * @param {object} opts
 * @param {Pick<Electron.SafeStorage, 'isEncryptionAvailable' | 'encryptString' | 'decryptString'>} opts.safeStorage
 * @param {string} opts.file  where the encrypted key lives
 */
function createKeyStore({ safeStorage, file }) {
  return {
    has() {
      return fs.existsSync(file)
    },

    /** Encrypt and store a key that passed checkApiKey; returns the key as stored (trimmed).
     * Throws if encryption isn't available. */
    save(key) {
      const checked = checkApiKey(key)
      if (!checked.ok) throw new Error(checked.error)
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error('macOS Keychain encryption is not available, so the key cannot be stored safely.')
      }
      const data = safeStorage.encryptString(checked.key)
      fs.mkdirSync(path.dirname(file), { recursive: true })
      const tmp = `${file}.tmp`
      fs.writeFileSync(tmp, data, { mode: 0o600 })
      fs.renameSync(tmp, file)
      return checked.key
    },

    /** The stored key, or null if there is none or it can't be decrypted (then ask again). */
    load() {
      let data
      try {
        data = fs.readFileSync(file)
      } catch {
        return null
      }
      try {
        const key = safeStorage.decryptString(data)
        return checkApiKey(key).ok ? key : null
      } catch {
        return null
      }
    },

    clear() {
      fs.rmSync(file, { force: true })
    },
  }
}

module.exports = { checkApiKey, createKeyStore }
