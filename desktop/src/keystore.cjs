const fs = require('node:fs')
const path = require('node:path')

function checkApiKey(input) {
  const key = typeof input === 'string' ? input.trim() : ''
  if (!key) return { ok: false, error: 'Paste your OpenAI API key.' }
  if (!key.startsWith('sk-')) return { ok: false, error: 'OpenAI API keys start with "sk-".' }
  if (/\s/.test(key)) return { ok: false, error: 'The key has a space or line break in it.' }
  if (!/^[\x21-\x7e]+$/.test(key)) return { ok: false, error: 'The key has characters an OpenAI key never has.' }
  if (key.length < 20 || key.length > 512) return { ok: false, error: "That doesn't look like a whole OpenAI API key." }
  return { ok: true, key }
}

function createKeyStore({ safeStorage, file }) {
  let loaded
  return {
    has() {
      return fs.existsSync(file)
    },

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
      loaded = checked.key
    },

    load() {
      if (loaded !== undefined) return loaded
      loaded = null
      let data
      try {
        data = fs.readFileSync(file)
      } catch {
        return null
      }
      try {
        const key = safeStorage.decryptString(data)
        loaded = checkApiKey(key).ok ? key : null
      } catch {
        return null
      }
      return loaded
    },

    clear() {
      fs.rmSync(file, { force: true })
      loaded = null
    },
  }
}

module.exports = { checkApiKey, createKeyStore }
