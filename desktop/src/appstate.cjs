// Small flags the app remembers between launches, in one JSON file in userData
// (app-state.json; tested in test/appstate.test.cjs). Today only `welcomeShown`; later one-time
// things (e.g. a "What's new" seen) belong here too, not in a file each.
//
// Never a reason to fail: a missing or damaged file reads as no flags set, and a write that fails
// returns its error, never throws (the flag is then simply not remembered; the caller logs why).

const fs = require('node:fs')
const path = require('node:path')

/** @param {string} file  e.g. <userData>/app-state.json */
function createAppState(file) {
  function read() {
    try {
      const data = JSON.parse(fs.readFileSync(file, 'utf8'))
      return data && typeof data === 'object' && !Array.isArray(data) ? data : {}
    } catch {
      return {}
    }
  }

  return {
    /** The flag's value, or undefined if it was never set. */
    get(name) {
      return read()[name]
    },
    /** Set one flag, keeping the others. Returns null, or the error if it couldn't be written. */
    set(name, value) {
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true })
        const tmp = `${file}.tmp`
        fs.writeFileSync(tmp, JSON.stringify({ ...read(), [name]: value }, null, 2))
        fs.renameSync(tmp, file) // atomic: a crash never leaves a half-written file
        return null
      } catch (err) {
        return err
      }
    },
  }
}

module.exports = { createAppState }
