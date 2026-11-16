const fs = require('node:fs')
const path = require('node:path')

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
    get(name) {
      return read()[name]
    },
    set(name, value) {
      try {
        fs.mkdirSync(path.dirname(file), { recursive: true })
        const tmp = `${file}.tmp`
        fs.writeFileSync(tmp, JSON.stringify({ ...read(), [name]: value }, null, 2))
        fs.renameSync(tmp, file)
        return null
      } catch (err) {
        return err
      }
    },
  }
}

module.exports = { createAppState }
