// Starts, watches and stops the Iris backend (the engine) for the desktop app. Plain Node, so it
// is tested without Electron (test/backend.test.cjs).
//
// Each start picks a free port and a fresh random token. The backend gets the app's pid in its
// environment (it exits if the app dies: iris/parent.py), and the token and the API key as one
// line of JSON on its stdin (iris/launch_secrets.py): any process of the same user can read
// another's environment (`ps eww <pid>`), but not its stdin.

const { spawn: spawnProcess } = require('node:child_process')
const crypto = require('node:crypto')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')
const { TOKEN_HEADER } = require('./routes.cjs')

const LOG_ROTATE_BYTES = 10 * 1024 * 1024
const LOG_DRAIN_MS = 2000 // after exit, how long the log waits for the rest of the output
const REQUEST_TIMEOUT_MS = 3000 // the app's own API requests (getJson, post)
const HEALTH_TIMEOUT_MS = 2000

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

function stoppedError() {
  return Object.assign(new Error('the backend was stopped while starting'), { stopped: true })
}

function newToken() {
  return crypto.randomBytes(32).toString('base64url')
}

/**
 * How to run the backend.
 * Packaged: the bundled Python, isolated (-I: no PYTHON* variables, user site or cwd on the
 * path), unbuffered (-u, so the log is live) and never writing bytecode into the app (-B; it is
 * precompiled at build time), in a clean environment, with userData as both
 * its data folder and cwd (a .env there still applies). `python -m iris`, not `bin/iris`: the
 * console script's shebang holds the build machine's path.
 * Development: `uv run` in the repo's backend/, through a login shell so uv is on PATH when the
 * app is opened with `open`; the repo's backend/.env and data/ apply, as with `make dev`.
 */
function backendCommand({ packaged, resourcesPath, repoDir, userData, port, token, parentPid, apiKey, env }) {
  const serve = ['-m', 'iris', 'serve', '--host', '127.0.0.1', '--port', String(port), '--secrets-from-stdin']
  const stdin = secretsLine({ apiKey, token })
  if (packaged) {
    const keep = ['HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG']
    const base = Object.fromEntries(keep.filter((k) => env[k]).map((k) => [k, env[k]]))
    return {
      cmd: path.join(resourcesPath, 'backend', 'python', 'bin', 'python3'),
      args: ['-I', '-u', '-B', ...serve],
      cwd: userData,
      env: { ...base, PATH: '/usr/bin:/bin:/usr/sbin:/sbin', IRIS_DATA_DIR: userData, IRIS_PARENT_PID: String(parentPid) },
      stdin,
    }
  }
  return {
    cmd: '/bin/zsh',
    args: ['-lc', `exec uv run python -u ${serve.join(' ')}`],
    cwd: path.join(repoDir, 'backend'),
    env: { ...env, IRIS_PARENT_PID: String(parentPid) },
    stdin,
  }
}

/** The line `iris serve --secrets-from-stdin` reads: the key (null if none) and the token. */
function secretsLine({ apiKey, token }) {
  return JSON.stringify({ openai_api_key: apiKey || null, api_token: token }) + '\n'
}

function requestWithToken(method, { port, token }, apiPath, timeoutMs) {
  return new Promise((resolve) => {
    const req = http.request(
      { method, host: '127.0.0.1', port, path: apiPath, headers: { [TOKEN_HEADER]: token }, timeout: timeoutMs },
      (res) => {
        let body = ''
        res.setEncoding('utf8')
        res.on('data', (c) => (body += c))
        res.on('end', () => resolve({ status: res.statusCode, body }))
        res.on('error', () => resolve(null))
      },
    )
    req.on('timeout', () => req.destroy())
    req.on('error', () => resolve(null))
    req.end()
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
 * The backend process. Emits 'exit' ({code, signal, expected, duringStart}) when it stops:
 * `expected` is false when it stopped on its own; `duringStart` is true when it never became
 * healthy (start() rejects for that). A crash is `!expected && !duringStart`.
 */
class Backend extends EventEmitter {
  /**
   * @param {object} opts
   * @param {(port: number, token: string) => {cmd: string, args: string[], cwd: string, env: object, stdin?: string}} opts.command
 *   `stdin` is written to the process's stdin, which is then closed.
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
    this.starting = null // the start() under way
    this.logClosed = Promise.resolve() // settles once the last process's log is written
  }

  /** Where the page's API requests go, or null while the backend isn't up. */
  info() {
    return this.ready
  }

  get running() {
    return this.child !== null
  }

  /**
   * Start and wait until healthy. Rejects if it exits or doesn't answer in time; the error has
   * `stopped: true` when stop() ended the start. One start at a time: calling start() again while
   * one is under way returns the same promise, so there is never a second backend process.
   */
  start() {
    if (this.starting) return this.starting
    if (this.child) return Promise.reject(new Error('backend already running'))
    this.stopping = false // set before the first await, so stop() from here on cancels this start
    this.starting = this._start().finally(() => (this.starting = null))
    return this.starting
  }

  async _start() {
    const port = await freePort()
    if (this.stopping) throw stoppedError()
    const token = newToken()
    const { cmd, args, cwd, env, stdin = '' } = this.command(port, token)
    fs.mkdirSync(path.dirname(this.logFile), { recursive: true })
    rotateLog(this.logFile)
    const out = fs.createWriteStream(this.logFile, { flags: 'a' })
    out.write(`\n--- ${new Date().toISOString()} starting the backend on port ${port}\n`)
    fs.mkdirSync(cwd, { recursive: true })
    const child = this.spawn(cmd, args, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] })
    this.child = child
    // A process that exits (or never starts) before reading its stdin makes the write fail with
    // EPIPE. Unhandled, that error would crash the app; the exit itself is reported below. The
    // line holds the key, so only the error code is logged.
    child.stdin.on('error', (err) => this.log(`[backend] couldn't write its stdin: ${err.code ?? 'error'}`))
    child.stdin.end(stdin)
    child.stdout.pipe(out, { end: false })
    child.stderr.pipe(out, { end: false })

    let healthy = false
    const exited = new Promise((resolve) => {
      child.once('error', (err) => {
        out.write(`--- could not start: ${err.message}\n`)
        resolve({ code: null, signal: null, error: err })
      })
      child.once('exit', (code, signal) => resolve({ code, signal }))
    })
    // The log ends once the process has exited and its output has been read to the end ('close'
    // follows 'exit' once stdout and stderr are drained), and stop() waits for that last write.
    // A grandchild holding the pipes (in development, uv runs Python as its own child) could keep
    // them open indefinitely, so the wait for 'close' is bounded.
    const closed = new Promise((resolve) => {
      child.once('close', resolve)
      child.once('error', resolve)
    })
    this.logClosed = exited.then(async ({ code, signal }) => {
      let timer
      await Promise.race([closed, new Promise((r) => (timer = setTimeout(r, LOG_DRAIN_MS)))])
      clearTimeout(timer)
      await new Promise((resolve) => out.end(`--- backend exited (code ${code}, signal ${signal})\n`, resolve))
    })
    exited.then(({ code, signal, error }) => {
      if (this.child === child) {
        this.child = null
        this.ready = null
      }
      const expected = this.stopping
      this.log(`[backend] exited (code ${code}, signal ${signal}${expected ? '' : ', unexpected'})`)
      // A backend that dies before it was ever healthy is reported by start()'s rejection, so
      // the app shows one "didn't start" message, not that and a crash.
      this.emit('exit', { code, signal, expected, duringStart: !healthy, error })
    })

    const started = Date.now()
    const deadline = started + this.startTimeoutMs
    let gone = null
    exited.then((e) => (gone = e))
    while (!gone) {
      if (this.stopping) break
      if ((await requestWithToken('GET', { port, token }, '/api/health', HEALTH_TIMEOUT_MS))?.status === 200) {
        if (this.stopping || gone) break
        healthy = true
        this.ready = { port, token }
        this.log(`[backend] up on port ${port} after ${((Date.now() - started) / 1000).toFixed(1)} s`)
        return this.ready
      }
      if (Date.now() > deadline) {
        this.stopping = true
        await this._terminate(child)
        throw new Error(`the backend did not start within ${Math.round(this.startTimeoutMs / 1000)} s`)
      }
      await new Promise((r) => setTimeout(r, 200))
    }
    if (this.stopping) throw stoppedError()
    throw new Error(
      gone.error ? `the backend could not start: ${gone.error.message}` : `the backend exited while starting (code ${gone.code})`,
    )
  }

  /**
   * Stop it: SIGTERM (it finishes cleanly), then SIGKILL after stopTimeoutMs. A start under way
   * is cancelled (it rejects with `stopped: true`), and stop() returns once no process is left.
   */
  async stop() {
    this.stopping = true
    this.ready = null
    await this._terminate(this.child)
    await this.starting?.catch(() => {})
    await this.logClosed
  }

  async _terminate(child) {
    if (!child || child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return
    const exited = new Promise((resolve) => child.once('exit', resolve))
    child.kill('SIGTERM')
    const timer = setTimeout(() => {
      this.log('[backend] did not stop in time: killing it')
      child.kill('SIGKILL')
    }, this.stopTimeoutMs)
    await exited
    clearTimeout(timer)
  }

  /** GET an API path with the token (the app's own checks, e.g. sessions in progress at quit):
   * its JSON on a 200, else null. */
  async getJson(apiPath, timeoutMs = REQUEST_TIMEOUT_MS) {
    const answer = this.ready && (await requestWithToken('GET', this.ready, apiPath, timeoutMs))
    if (answer?.status !== 200) return null
    try {
      return JSON.parse(answer.body)
    } catch {
      return null
    }
  }

  /** POST to an API path with the token and no body (the app's own actions, e.g. stopping a
   * recording at quit): the status code, or 0 if the backend didn't answer. */
  async post(apiPath, timeoutMs = REQUEST_TIMEOUT_MS) {
    return (this.ready && (await requestWithToken('POST', this.ready, apiPath, timeoutMs)))?.status ?? 0
  }
}

module.exports = { Backend, REQUEST_TIMEOUT_MS, backendCommand, freePort, newToken, secretsLine }
