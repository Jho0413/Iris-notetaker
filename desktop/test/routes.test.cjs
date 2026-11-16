const assert = require('node:assert/strict')
const path = require('node:path')
const { test } = require('node:test')
const routes = require('../src/routes.cjs')

test('API paths are forwarded with their query', () => {
  assert.deepEqual(routes.route('app://iris/api/sessions'), { kind: 'api', path: '/api/sessions' })
  assert.deepEqual(routes.route('app://iris/api/live/abc/frame?t=1.5'), { kind: 'api', path: '/api/live/abc/frame?t=1.5' })
  assert.deepEqual(routes.route('app://iris/api'), { kind: 'api', path: '/api' })
})

test('other paths are static files or client routes', () => {
  assert.deepEqual(routes.route('app://iris/'), { kind: 'static', pathname: '/' })
  assert.deepEqual(routes.route('app://iris/assets/index-abc.js'), { kind: 'static', pathname: '/assets/index-abc.js' })
  assert.deepEqual(routes.route('app://iris/sessions/abc123'), { kind: 'static', pathname: '/sessions/abc123' })
  assert.deepEqual(routes.route('app://iris/apidocs'), { kind: 'static', pathname: '/apidocs' })
  assert.deepEqual(routes.route('app://iris/a%20b.svg'), { kind: 'static', pathname: '/a b.svg' })
})

test('other hosts, schemes, credentials and malformed URLs are denied', () => {
  for (const url of [
    'app://other/api/sessions',
    'app://iris.evil/',
    'http://iris/api/sessions',
    'file:///etc/passwd',
    'app://user:pw@iris/api/sessions',
    'app://iris/%E0%A4%A',
    'app://iris/a%00b',
    'not a url',
  ]) {
    assert.equal(routes.route(url).kind, 'deny', url)
  }
})

test('the forward URL always stays on the backend', () => {
  assert.equal(routes.forwardUrl(8123, '/api/sessions?x=1'), 'http://127.0.0.1:8123/api/sessions?x=1')
  for (const p of ['/api//evil.example/x', '/api/@evil.example', '/api/..//evil', '/api/x#frag', '/api\\..\\x']) {
    const u = new URL(routes.forwardUrl(8123, p))
    assert.equal(u.host, '127.0.0.1:8123', p)
    assert.equal(u.protocol, 'http:', p)
  }
  assert.throws(() => routes.forwardUrl(8123, '//evil.example/api'))
  assert.throws(() => routes.forwardUrl(8123, 'http://evil.example/api'))
  assert.throws(() => routes.forwardUrl(0, '/api/x'))
  assert.throws(() => routes.forwardUrl(70000, '/api/x'))
  assert.throws(() => routes.forwardUrl('8123', '/api/x'))
})

test('a routed API path cannot leave the backend either', () => {
  for (const url of ['app://iris/api//evil.example/', 'app://iris/api/%2F%2Fevil.example', 'app://iris/api/..%2F..%2F']) {
    const r = routes.route(url)
    assert.equal(r.kind, 'api', url)
    assert.equal(new URL(routes.forwardUrl(9000, r.path)).host, '127.0.0.1:9000', url)
  }
})

test('forwarded headers: the token is always ours, hop-by-hop headers dropped', () => {
  const h = routes.forwardHeaders(
    [
      ['Content-Type', 'application/json'],
      ['X-Iris-Token', 'from-the-page'],
      ['x-iris-token', 'again'],
      ['Connection', 'keep-alive'],
      ['Host', 'evil.example'],
      ['Transfer-Encoding', 'chunked'],
      ['Accept', 'text/event-stream'],
    ],
    'real-token',
  )
  assert.equal(h.get('x-iris-token'), 'real-token')
  assert.equal(h.get('content-type'), 'application/json')
  assert.equal(h.get('accept'), 'text/event-stream')
  for (const name of ['connection', 'host', 'transfer-encoding']) assert.equal(h.get(name), null, name)
})

test('redirects stay inside app://iris', () => {
  assert.equal(routes.rewriteLocation('http://127.0.0.1:8000/api/sessions?a=1', 8000), 'app://iris/api/sessions?a=1')
  assert.equal(routes.rewriteLocation('/api/sessions', 8000), 'app://iris/api/sessions')
  assert.equal(routes.rewriteLocation('https://evil.example/', 8000), null)
  assert.equal(routes.rewriteLocation('http://127.0.0.1:9999/api', 8000), null)
  assert.equal(routes.rewriteLocation('//evil.example/x', 8000), null)
})

test('files resolve inside dist only', () => {
  const dist = path.resolve('/opt/iris/dist')
  assert.equal(routes.resolveInDist(dist, '/assets/a.js'), path.join(dist, 'assets/a.js'))
  assert.equal(routes.resolveInDist(dist, '/'), dist)
  assert.equal(routes.resolveInDist(dist, '//etc/passwd'), path.join(dist, 'etc/passwd'))
  for (const p of ['/../secret', '/assets/../../secret', '/..', '/../dist-other/x']) {
    assert.equal(routes.resolveInDist(dist, p), null, p)
  }
})

test('isInside', () => {
  assert.ok(routes.isInside('/a/b', '/a/b'))
  assert.ok(routes.isInside('/a/b', '/a/b/c'))
  assert.ok(routes.isInside('/a/b', '/a/b/..c'))
  assert.ok(!routes.isInside('/a/b', '/a/bc'))
  assert.ok(!routes.isInside('/a/b', '/a'))
  assert.ok(!routes.isInside('/a/b', '/x'))
})

test('client routes vs missing files', () => {
  assert.ok(routes.isClientRoute('/sessions/abc'))
  assert.ok(routes.isClientRoute('/'))
  assert.ok(!routes.isClientRoute('/assets/missing.js'))
  assert.ok(!routes.isClientRoute('/favicon.ico'))
})

test('content types', () => {
  assert.equal(routes.contentType('/x/index.html'), 'text/html; charset=utf-8')
  assert.equal(routes.contentType('/x/a.JS'), 'text/javascript; charset=utf-8')
  assert.equal(routes.contentType('/x/KaTeX_Main-Regular.woff2'), 'font/woff2')
  assert.equal(routes.contentType('/x/unknown.bin'), 'application/octet-stream')
})
