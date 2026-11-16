const assert = require('node:assert/strict')
const fs = require('node:fs')
const http = require('node:http')
const os = require('node:os')
const path = require('node:path')
const { after, before, test } = require('node:test')
const { handleAppProtocol, registerScheme } = require('../src/handler.cjs')

const TOKEN = 'launch-token'
let dist, outside, server, port, handle
let backendUp = true
const seen = []

before(async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'iris-handler-'))
  dist = path.join(root, 'dist')
  outside = path.join(root, 'secret.txt')
  fs.mkdirSync(path.join(dist, 'assets'), { recursive: true })
  fs.writeFileSync(path.join(dist, 'index.html'), '<!doctype html><script>theme()</script><div id="root"></div>')
  fs.writeFileSync(path.join(dist, 'assets', 'app.js'), 'console.log(1)')
  fs.writeFileSync(path.join(dist, 'assets', 'KaTeX_Main.woff2'), 'font')
  fs.writeFileSync(outside, 'secret')
  fs.symlinkSync(outside, path.join(dist, 'assets', 'link.txt'))
  fs.symlinkSync(root, path.join(dist, 'up'))

  server = http.createServer(async (req, res) => {
    const chunks = []
    for await (const c of req) chunks.push(c)
    const body = Buffer.concat(chunks)
    seen.push({ method: req.method, url: req.url, headers: req.headers, bytes: body.length })
    if (req.headers['x-iris-token'] !== TOKEN) {
      res.writeHead(401, { 'content-type': 'application/json' })
      return res.end('{"detail":"missing or wrong API token"}')
    }
    if (req.url === '/api/events') {
      res.writeHead(200, { 'content-type': 'text/event-stream' })
      res.write('event: a\ndata: 1\n\n')
      setTimeout(() => res.write('event: b\ndata: 2\n\n'), 150)
      res.on('close', () => seen.push({ closed: '/api/events' }))
      return
    }
    if (req.url === '/api/redirect') {
      res.writeHead(307, { location: `http://127.0.0.1:${port}/api/sessions` })
      return res.end()
    }
    if (req.url === '/api/redirect-out') {
      res.writeHead(307, { location: 'https://evil.example/' })
      return res.end()
    }
    if (req.method === 'DELETE') {
      res.writeHead(204)
      return res.end()
    }
    res.writeHead(200, { 'content-type': 'application/json', 'set-cookie': ['a=1', 'b=2'] })
    res.end(JSON.stringify({ method: req.method, url: req.url, bytes: body.length }))
  })
  await new Promise((r) => server.listen(0, '127.0.0.1', r))
  port = server.address().port

  const protocol = { handle: (scheme, fn) => (assert.equal(scheme, 'app'), (handle = fn)) }
  handleAppProtocol({ protocol, distDir: dist, backend: () => (backendUp ? { port, token: TOKEN } : null), log: () => {} })
})

after(() => server.close())

const get = (url, init) => handle(new Request(url, init))

test('registers app:// as a privileged standard, secure, streaming scheme without CORS', () => {
  let schemes
  registerScheme({ registerSchemesAsPrivileged: (s) => (schemes = s) })
  assert.equal(schemes.length, 1)
  assert.equal(schemes[0].scheme, 'app')
  const p = schemes[0].privileges
  assert.ok(p.standard && p.secure && p.supportFetchAPI && p.stream)
  assert.ok(!p.corsEnabled && !p.bypassCSP)
})

test('index.html at / with the CSP, and for client routes', async () => {
  for (const url of ['app://iris/', 'app://iris/sessions/abc', 'app://iris/live', 'app://iris/index.html']) {
    const res = await get(url)
    assert.equal(res.status, 200, url)
    assert.equal(res.headers.get('content-type'), 'text/html; charset=utf-8')
    assert.match(res.headers.get('content-security-policy'), /script-src 'self' 'sha256-/)
    assert.match(await res.text(), /id="root"/)
  }
})

test('built files are served with their type', async () => {
  const js = await get('app://iris/assets/app.js')
  assert.equal(js.status, 200)
  assert.equal(js.headers.get('content-type'), 'text/javascript; charset=utf-8')
  assert.equal(await js.text(), 'console.log(1)')
  assert.equal((await get('app://iris/assets/KaTeX_Main.woff2')).headers.get('content-type'), 'font/woff2')
})

test('a missing file is a 404, not index.html', async () => {
  assert.equal((await get('app://iris/assets/missing.js')).status, 404)
})

test('nothing outside dist is served: traversal, encoded traversal, symlinks', async () => {
  for (const url of [
    'app://iris/../secret.txt',
    'app://iris/%2e%2e/secret.txt',
    'app://iris/..%2fsecret.txt',
    'app://iris/assets/..%2f..%2fsecret.txt',
    'app://iris/%2F..%2F..%2Fsecret.txt',
    'app://iris/assets/link.txt',
    'app://iris/up/secret.txt',
  ]) {
    const res = await get(url)
    const body = await res.text()
    assert.ok(!body.includes('secret'), `${url} leaked the file`)
    assert.ok(res.status === 404 || /id="root"/.test(body), `${url}: ${res.status}`)
  }
})

test('other hosts are refused', async () => {
  assert.equal((await get('app://other/api/sessions')).status, 404)
  assert.equal(seen.filter((r) => r.url === '/api/sessions' && r.headers?.host?.includes('other')).length, 0)
})

test('API requests reach the backend with the token, the page’s own token replaced', async () => {
  seen.length = 0
  const res = await get('app://iris/api/sessions?x=1', { headers: { 'X-Iris-Token': 'forged' } })
  assert.equal(res.status, 200)
  assert.deepEqual(await res.json(), { method: 'GET', url: '/api/sessions?x=1', bytes: 0 })
  assert.equal(seen[0].headers['x-iris-token'], TOKEN)
  assert.equal(seen[0].headers.host, `127.0.0.1:${port}`)
  assert.deepEqual(res.headers.getSetCookie(), ['a=1', 'b=2'])
  assert.match(res.headers.get('content-security-policy'), /default-src 'none'.*sandbox/)
})

test('request bodies are streamed through', async () => {
  seen.length = 0
  const size = 5 * 1024 * 1024
  let sent = 0
  const body = new ReadableStream({
    pull(c) {
      if (sent >= size) return c.close()
      const n = Math.min(64 * 1024, size - sent)
      sent += n
      c.enqueue(new Uint8Array(n))
    },
  })
  const res = await get('app://iris/api/sessions', { method: 'POST', body, duplex: 'half' })
  assert.deepEqual(await res.json(), { method: 'POST', url: '/api/sessions', bytes: size })
  assert.equal(seen[0].headers['transfer-encoding'], 'chunked')
  const fixed = await get('app://iris/api/live/x/frame?t=1&share=1', { method: 'POST', body: new Uint8Array(1000) })
  assert.deepEqual(await fixed.json(), { method: 'POST', url: '/api/live/x/frame?t=1&share=1', bytes: 1000 })
})

test('a POST with no body is sent as one, with no chunked encoding (running a session again)', async () => {
  seen.length = 0
  const res = await get('app://iris/api/sessions/x/run', { method: 'POST' })
  assert.deepEqual(await res.json(), { method: 'POST', url: '/api/sessions/x/run', bytes: 0 })
  assert.equal(seen[0].headers['transfer-encoding'], undefined)
  assert.equal(seen[0].headers['content-length'], '0')
})

test('server-sent events arrive as they are sent, and closing the stream closes the backend request', async () => {
  seen.length = 0
  const res = await get('app://iris/api/events')
  assert.equal(res.headers.get('content-type'), 'text/event-stream')
  const reader = res.body.getReader()
  const t0 = Date.now()
  const first = new TextDecoder().decode((await reader.read()).value)
  assert.match(first, /event: a/)
  assert.ok(Date.now() - t0 < 140, 'the first event did not wait for the second')
  const second = new TextDecoder().decode((await reader.read()).value)
  assert.match(second, /event: b/)
  await reader.cancel()
  for (let i = 0; i < 50 && !seen.some((r) => r.closed); i++) await new Promise((r) => setTimeout(r, 10))
  assert.ok(seen.some((r) => r.closed === '/api/events'), 'the backend saw the client go away')
})

test('204 responses have no body', async () => {
  const res = await get('app://iris/api/sessions/x', { method: 'DELETE' })
  assert.equal(res.status, 204)
  assert.equal(res.body, null)
})

test('redirects are kept inside app://iris', async () => {
  const inside = await get('app://iris/api/redirect')
  assert.equal(inside.status, 307)
  assert.equal(inside.headers.get('location'), 'app://iris/api/sessions')
  const out = await get('app://iris/api/redirect-out')
  assert.equal(out.headers.get('location'), null)
})

test('503 while the backend is not running, 502 when it does not answer', async () => {
  backendUp = false
  try {
    const res = await get('app://iris/api/sessions')
    assert.equal(res.status, 503)
    assert.match((await res.json()).detail, /not running/)
    assert.equal((await get('app://iris/')).status, 200)
  } finally {
    backendUp = true
  }
  const closed = http.createServer()
  await new Promise((r) => closed.listen(0, '127.0.0.1', r))
  const deadPort = closed.address().port
  await new Promise((r) => closed.close(r))
  const protocol = { handle: (_s, fn) => (handle2 = fn) }
  let handle2
  handleAppProtocol({ protocol, distDir: dist, backend: () => ({ port: deadPort, token: TOKEN }), log: () => {} })
  const res = await handle2(new Request('app://iris/api/sessions'))
  assert.equal(res.status, 502)
})
