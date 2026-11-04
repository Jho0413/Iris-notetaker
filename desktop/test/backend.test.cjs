const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')
const { Backend, backendCommand, freePort, newToken } = require('../src/backend.cjs')

const FAKE = path.join(__dirname, 'fake-backend.cjs')

function fakeBackend(mode, opts = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'iris-backend-'))
  const logFile = path.join(dir, 'logs', 'backend.log')
  const backend = new Backend({
    command: (port, token) => ({
      cmd: process.execPath,
      args: [FAKE, mode, String(port)],
      cwd: path.join(dir, 'data'),
      env: { PATH: process.env.PATH, IRIS_API_TOKEN: token, IRIS_PARENT_PID: String(process.pid) },
    }),
    logFile,
    startTimeoutMs: 3000,
    stopTimeoutMs: 500,
    ...opts,
  })
  return { backend, logFile }
}

function exitEvent(backend) {
  return new Promise((resolve) => backend.once('exit', resolve))
}

test('freePort and newToken', async () => {
  const port = await freePort()
  assert.ok(port > 1024 && port < 65536)
  const a = newToken()
  assert.ok(a.length >= 40)
  assert.notEqual(a, newToken())
})

test('the packaged command: bundled python, isolated, clean environment, data in userData', () => {
  const c = backendCommand({
    packaged: true,
    resourcesPath: '/Applications/Iris.app/Contents/Resources',
    repoDir: '/repo',
    userData: '/Users/me/Library/Application Support/Iris',
    port: 4321,
    token: 'tok',
    parentPid: 99,
    apiKey: 'sk-key',
    env: { HOME: '/Users/me', PATH: '/opt/homebrew/bin:/usr/bin', PYTHONPATH: '/evil', IRIS_DATA_DIR: '/elsewhere', TMPDIR: '/tmp/x', LANG: 'en_GB.UTF-8' },
  })
  assert.equal(c.cmd, '/Applications/Iris.app/Contents/Resources/backend/python/bin/python3')
  assert.deepEqual(c.args, ['-I', '-u', '-B', '-m', 'iris', 'serve', '--host', '127.0.0.1', '--port', '4321'])
  assert.equal(c.cwd, '/Users/me/Library/Application Support/Iris')
  assert.deepEqual(c.env, {
    HOME: '/Users/me',
    TMPDIR: '/tmp/x',
    LANG: 'en_GB.UTF-8',
    PATH: '/usr/bin:/bin:/usr/sbin:/sbin',
    IRIS_DATA_DIR: '/Users/me/Library/Application Support/Iris',
    OPENAI_API_KEY: 'sk-key',
    IRIS_API_TOKEN: 'tok',
    IRIS_PARENT_PID: '99',
  })
})

test('the development command: uv in the repo backend, its .env supplies the key unless one is stored', () => {
  const c = backendCommand({ packaged: false, repoDir: '/repo', userData: '/u', port: 4321, token: 'tok', parentPid: 99, apiKey: null, env: { PATH: '/p' } })
  assert.equal(c.cmd, '/bin/zsh')
  assert.deepEqual(c.args, ['-lc', 'exec uv run python -u -m iris serve --host 127.0.0.1 --port 4321'])
  assert.equal(c.cwd, '/repo/backend')
  assert.equal(c.env.OPENAI_API_KEY, undefined)
  assert.equal(c.env.IRIS_API_TOKEN, 'tok')
  assert.equal(c.env.PATH, '/p')
})

test('start waits for health with the token, stop is clean and expected', async () => {
  const { backend, logFile } = fakeBackend('ok')
  assert.equal(backend.info(), null)
  const ready = await backend.start()
  assert.deepEqual(backend.info(), ready)
  assert.ok(backend.running)
  // Without the token the backend refuses; the app's own calls carry it.
  const status = await new Promise((r) => http.get({ host: '127.0.0.1', port: ready.port, path: '/api/sessions' }, (res) => r(res.statusCode)))
  assert.equal(status, 401)
  assert.deepEqual(await backend.getJson('/api/sessions'), [{ id: 'a', status: 'processing' }])
  await assert.rejects(backend.start(), /already running/)
  const exit = exitEvent(backend)
  await backend.stop()
  assert.equal((await exit).expected, true)
  assert.equal(backend.info(), null)
  assert.equal(backend.running, false)
  assert.equal(await backend.getJson('/api/sessions'), null)
  const logged = fs.readFileSync(logFile, 'utf8')
  assert.match(logged, /fake backend ok env-token=set parent=\d+/)
  assert.match(logged, /backend exited/)
  await backend.stop() // stopping twice is fine
})

test('each start gets a new port and token', async () => {
  const { backend } = fakeBackend('ok')
  const a = await backend.start()
  await backend.stop()
  const b = await backend.start()
  await backend.stop()
  assert.notEqual(a.token, b.token)
})

test('a crash while starting rejects; a crash later is reported as unexpected', async () => {
  const { backend } = fakeBackend('crash')
  const startExit = exitEvent(backend)
  await assert.rejects(backend.start(), /exited while starting \(code 3\)/)
  assert.equal(backend.running, false)
  const e0 = await startExit
  assert.equal(e0.expected, false)
  assert.equal(e0.duringStart, true, 'reported by the rejection, not as a crash')

  const ok = fakeBackend('ok').backend
  const ready = await ok.start()
  const exit = exitEvent(ok)
  process.kill(ok.child.pid, 'SIGKILL')
  const e = await exit
  assert.equal(e.expected, false)
  assert.equal(e.duringStart, false) // a real crash
  assert.equal(e.signal, 'SIGKILL')
  assert.equal(ok.info(), null)
  assert.ok(ready.port)
})

test('a backend that never answers times out and is stopped', async () => {
  const { backend } = fakeBackend('slow', { startTimeoutMs: 600 })
  await assert.rejects(backend.start(), /did not start within/)
  assert.equal(backend.running, false)
})

test('a backend ignoring SIGTERM is killed', async () => {
  const { backend } = fakeBackend('stubborn')
  await backend.start()
  const t0 = Date.now()
  const exit = exitEvent(backend)
  await backend.stop()
  assert.equal((await exit).signal, 'SIGKILL')
  assert.ok(Date.now() - t0 >= 450)
})

test('a command that cannot run rejects with the reason', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'iris-backend-'))
  const backend = new Backend({
    command: () => ({ cmd: path.join(dir, 'no-such-python'), args: [], cwd: dir, env: {} }),
    logFile: path.join(dir, 'backend.log'),
    startTimeoutMs: 2000,
  })
  await assert.rejects(backend.start(), /could not start: .*ENOENT/)
  assert.equal(backend.running, false)
})

function alive(pid) {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

test('concurrent starts share one process, and stop leaves none', async () => {
  let spawned = 0
  const { backend } = fakeBackend('ok')
  const realSpawn = backend.spawn
  const pids = []
  backend.spawn = (...args) => {
    spawned++
    const child = realSpawn(...args)
    pids.push(child.pid)
    return child
  }
  const [a, b] = await Promise.all([backend.start(), backend.start()])
  assert.deepEqual(a, b)
  assert.equal(spawned, 1)
  await backend.stop()
  assert.ok(!alive(pids[0]))
})

test('stop during a slow start cancels it: start rejects as stopped, no process is left', async () => {
  const { backend } = fakeBackend('slow', { startTimeoutMs: 10_000 })
  const exit = exitEvent(backend)
  const starting = backend.start()
  await new Promise((r) => setTimeout(r, 300)) // spawned, not healthy
  const pid = backend.child.pid
  await backend.stop()
  await assert.rejects(starting, (err) => err.stopped === true)
  assert.equal((await exit).expected, true)
  assert.ok(!alive(pid))
  assert.equal(backend.running, false)
  // And it can start again afterwards.
  backend.command = fakeBackend('ok').backend.command
  await backend.start()
  await backend.stop()
})

test('stop before the process is spawned cancels the start too', async () => {
  let spawned = 0
  const { backend } = fakeBackend('ok')
  const realSpawn = backend.spawn
  backend.spawn = (...args) => (spawned++, realSpawn(...args))
  const starting = backend.start()
  const stopping = backend.stop() // while start awaits a free port
  await assert.rejects(starting, (err) => err.stopped === true)
  await stopping
  assert.equal(spawned, 0)
  assert.equal(backend.running, false)
})
