// Starts, watches and stops the Iris backend (the engine) for the desktop app. Plain Node, so it
// is tested without Electron (test/backend.test.cjs).
//
// Each start picks a free port and a fresh random token. The backend gets the token, the app's
// pid (it exits if the app dies: iris/parent.py) and the API key in its environment.

const { spawn: spawnProcess } = require('node:child_process')
const crypto = require('node:crypto')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')

const TOKEN_HEADER = 'x-iris-token'
const LOG_ROTATE_BYTES = 10 * 1024 * 1024

/** A port nothing is listening on right now, on 127.0.0.1. */
function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

function newToken() {
  return crypto.randomBytes(32).toString('base64url')
}

/**
 * How to run the backend.
 * Packaged: the bundled Python, isolated (-I: no PYTHON* variables, user site or cwd on the
 * path) and unbuffered (-u, so the log is live), in a clean environment, with userData as both
 * its data folder and cwd (a .env there still applies). `python -m iris`, not `bin/iris`: the
 * console script's shebang holds the build machine's path.
 * Development: `uv run` in the repo's backend/, through a login shell so uv is on PATH when the
 * app is opened with `open`; the repo's backend/.env and data/ apply, as with `make dev`.
 */
function backendCommand({ packaged, resourcesPath, repoDir, userData, port, token, parentPid, apiKey, env }) {
  const serve = ['-m', 'iris', 'serve', '--host', '127.0.0.1', '--port', String(port)]
  const iris = { IRIS_API_TOKEN: token, IRIS_PARENT_PID: String(parentPid) }
  const key = apiKey ? { OPENAI_API_KEY: apiKey } : {}
  if (packaged) {
    const keep = ['HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG']
    const base = Object.fromEntries(keep.filter((k) => env[k]).map((k) => [k, env[k]]))
    return {
      cmd: path.join(resourcesPath, 'backend', 'python', 'bin', 'python3'),
      args: ['-I', '-u', ...serve],
      cwd: userData,
      env: { ...base, PATH: '/usr/bin:/bin:/usr/sbin:/sbin', IRIS_DATA_DIR: userData, ...key, ...iris },
    }
  }
  return {
    cmd: '/bin/zsh',
    args: ['-lc', `exec uv run python -u ${serve.join(' ')}`],
    cwd: path.join(repoDir, 'backend'),
    env: { ...env, ...key, ...iris },
  }
}

/** GET /api/health with the token: true once it answers 200. */
function checkHealth(port, token, timeoutMs = 2000) {
  return new Promise((resolve) => {
    const req = http.get(
      { host: '127.0.0.1', port, path: '/api/health', headers: { [TOKEN_HEADER]: token }, timeout: timeoutMs },
      (res) => {
        res.resume()
        resolve(res.statusCode === 200)
      },
    )
    req.on('timeout', () => req.destroy())
    req.on('error', () => resolve(false))
  })
}

/** Move a big log aside (one old copy kept), so logs don't grow forever. */
function rotateLog(file) {
  try {
    if (fs.statSync(file).size > LOG_ROTATE_BYTES) fs.renameSync(file, `${file}.1`)
  } catch {
    // no log yet
  }
}

/**
 * The backend process. Emits 'exit' ({code, signal, expected}) when it stops: `expected` is false
 * when it stopped on its own (a crash), so the app can offer a restart.
 */
class Backend extends EventEmitter {
  /**
   * @param {object} opts
   * @param {(port: number, token: string) => {cmd: string, args: string[], cwd: string, env: object}} opts.command
   * @param {string} opts.logFile
   * @param {(line: string) => void} [opts.log]
   * @param {number} [opts.startTimeoutMs]  first launch of a fresh install takes ~40 s (macOS checks each native library)
   * @param {number} [opts.stopTimeoutMs]   then SIGKILL
   * @param {typeof spawnProcess} [opts.spawn]
   */
  constructor({ command, logFile, log = () => {}, startTimeoutMs = 180_000, stopTimeoutMs = 10_000, spawn = spawnProcess }) {
    super()
    this.command = command
    this.logFile = logFile
    this.log = log
    this.startTimeoutMs = startTimeoutMs
    this.stopTimeoutMs = stopTimeoutMs
    this.spawn = spawn
    this.child = null
    this.ready = null // {port, token} once healthy
    this.stopping = false
  }

  /** Where the page's API requests go, or null while the backend isn't up. */
  info() {
    return this.ready
  }

  get running() {
    return this.child !== null
  }

  /** Start and wait until healthy. Rejects if it exits or doesn't answer in time. */
  async start() {
    if (this.child) throw new Error('backend already running')
    this.stopping = false
    const port = await freePort()
    const token = newToken()
    const { cmd, args, cwd, env } = this.command(port, token)
    fs.mkdirSync(path.dirname(this.logFile), { recursive: true })
    rotateLog(this.logFile)
    const out = fs.createWriteStream(this.logFile, { flags: 'a' })
    out.write(`\n--- ${new Date().toISOString()} starting the backend on port ${port}\n`)
    fs.mkdirSync(cwd, { recursive: true })
    const child = this.spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
    this.child = child
    child.stdout.pipe(out, { end: false })
    child.stderr.pipe(out, { end: false })

    const exited = new Promise((resolve) => {
      child.once('error', (err) => {
        out.write(`--- could not start: ${err.message}\n`)
        resolve({ code: null, signal: null, error: err })
      })
      child.once('exit', (code, signal) => resolve({ code, signal }))
    })
    exited.then(({ code, signal, error }) => {
      out.end(`--- backend exited (code ${code}, signal ${signal})\n`)
      if (this.child === child) {
        this.child = null
        this.ready = null
      }
      const expected = this.stopping
      this.log(`[backend] exited (code ${code}, signal ${signal}${expected ? '' : ', unexpected'})`)
      this.emit('exit', { code, signal, expected, error })
    })

    const started = Date.now()
    const deadline = started + this.startTimeoutMs
    let gone = null
    exited.then((e) => (gone = e))
    while (!gone) {
      if (await checkHealth(port, token)) {
        this.ready = { port, token }
        this.log(`[backend] up on port ${port} after ${((Date.now() - started) / 1000).toFixed(1)} s`)
        return this.ready
      }
      if (Date.now() > deadline) {
        await this.stop()
        throw new Error(`the backend did not start within ${Math.round(this.startTimeoutMs / 1000)} s`)
      }
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error(
      gone.error ? `the backend could not start: ${gone.error.message}` : `the backend exited while starting (code ${gone.code})`,
    )
  }

  /** Stop it: SIGTERM (it finishes cleanly), then SIGKILL after stopTimeoutMs. */
  async stop() {
    const child = this.child
    if (!child) return
    this.stopping = true
    this.ready = null
    if (child.exitCode !== null || child.signalCode !== null) return
    const exited = new Promise((resolve) => child.once('exit', resolve))
    child.kill('SIGTERM')
    const timer = setTimeout(() => {
      this.log('[backend] did not stop in time: killing it')
      child.kill('SIGKILL')
    }, this.stopTimeoutMs)
    await exited
    clearTimeout(timer)
  }

  /** GET an API path with the token (the app's own checks, e.g. sessions in progress at quit). */
  getJson(apiPath, timeoutMs = 3000) {
    const ready = this.ready
    if (!ready) return Promise.resolve(null)
    return new Promise((resolve) => {
      const req = http.get(
        { host: '127.0.0.1', port: ready.port, path: apiPath, headers: { [TOKEN_HEADER]: ready.token }, timeout: timeoutMs },
        (res) => {
          let body = ''
          res.setEncoding('utf8')
          res.on('data', (c) => (body += c))
          res.on('end', () => {
            try {
              resolve(res.statusCode === 200 ? JSON.parse(body) : null)
            } catch {
              resolve(null)
            }
          })
        },
      )
      req.on('timeout', () => req.destroy())
      req.on('error', () => resolve(null))
    })
  }
}

module.exports = { Backend, backendCommand, checkHealth, freePort, newToken }
