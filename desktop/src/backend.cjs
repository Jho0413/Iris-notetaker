const { spawn: spawnProcess } = require('node:child_process')
const crypto = require('node:crypto')
const { EventEmitter } = require('node:events')
const fs = require('node:fs')
const http = require('node:http')
const net = require('node:net')
const path = require('node:path')
const { TOKEN_HEADER } = require('./routes.cjs')

const LOG_ROTATE_BYTES = 10 * 1024 * 1024
const LOG_DRAIN_MS = 2000
const REQUEST_TIMEOUT_MS = 3000
const HEALTH_TIMEOUT_MS = 2000

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

function rotateLog(file) {
  try {
    if (fs.statSync(file).size > LOG_ROTATE_BYTES) fs.renameSync(file, `${file}.1`)
  } catch {
  }
}

class Backend extends EventEmitter {
  constructor({ command, logFile, log = () => {}, startTimeoutMs = 180_000, stopTimeoutMs = 10_000, spawn = spawnProcess }) {
    super()
    this.command = command
    this.logFile = logFile
    this.log = log
    this.startTimeoutMs = startTimeoutMs
    this.stopTimeoutMs = stopTimeoutMs
    this.spawn = spawn
    this.child = null
    this.ready = null
    this.stopping = false
    this.starting = null
    this.logClosed = Promise.resolve()
  }

  info() {
    return this.ready
  }

  get running() {
    return this.child !== null
  }

  start() {
    if (this.starting) return this.starting
    if (this.child) return Promise.reject(new Error('backend already running'))
    this.stopping = false
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

  async getJson(apiPath, timeoutMs = REQUEST_TIMEOUT_MS) {
    const answer = this.ready && (await requestWithToken('GET', this.ready, apiPath, timeoutMs))
    if (answer?.status !== 200) return null
    try {
      return JSON.parse(answer.body)
    } catch {
      return null
    }
  }

  async post(apiPath, timeoutMs = REQUEST_TIMEOUT_MS) {
    return (this.ready && (await requestWithToken('POST', this.ready, apiPath, timeoutMs)))?.status ?? 0
  }
}

module.exports = { Backend, REQUEST_TIMEOUT_MS, backendCommand, freePort, newToken, secretsLine }
