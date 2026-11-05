const assert = require('node:assert/strict')
const { test } = require('node:test')
const { checkKeyWithOpenAI, MODELS_URL, REJECTED, UNREACHABLE, CANT_CHECK } = require('../src/keycheck.cjs')

const KEY = 'sk-test-0123456789abcdefghij'

/** A fake fetch answering with `status`, recording what it was asked. */
function fakeFetch(status) {
  const calls = []
  const fetch = async (url, init) => {
    calls.push({ url, init })
    return new Response('{"data": []}', { status })
  }
  return { fetch, calls }
}

test('a working key passes, checked with one models request carrying the key', async () => {
  const { fetch, calls } = fakeFetch(200)
  assert.deepEqual(await checkKeyWithOpenAI(KEY, { fetch }), { ok: true })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].url, MODELS_URL)
  assert.equal(calls[0].init.headers.Authorization, `Bearer ${KEY}`)
  assert.ok(calls[0].init.signal, 'the request has a timeout')
})

test('a wrong, revoked or restricted key is rejected', async () => {
  for (const status of [401, 403]) {
    const { ok, error } = await checkKeyWithOpenAI(KEY, fakeFetch(status))
    assert.deepEqual({ ok, error }, { ok: false, error: REJECTED }, status)
  }
})

test('OpenAI errors and rate limits say the key couldn’t be checked, not that it is wrong', async () => {
  for (const status of [429, 500, 502, 503]) {
    const { ok, error } = await checkKeyWithOpenAI(KEY, fakeFetch(status))
    assert.deepEqual({ ok, error }, { ok: false, error: CANT_CHECK }, status)
  }
})

test('no connection says OpenAI couldn’t be reached', async () => {
  const fetch = async () => {
    throw new TypeError('fetch failed')
  }
  const { ok, error } = await checkKeyWithOpenAI(KEY, { fetch })
  assert.deepEqual({ ok, error }, { ok: false, error: UNREACHABLE })
})

test('a request that hangs times out as unreachable', async () => {
  const fetch = (_url, init) =>
    new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(init.signal.reason)))
  // AbortSignal.timeout's timer doesn't keep Node alive on its own; hold the test open meanwhile.
  const hold = setTimeout(() => {}, 1000)
  try {
    const { ok, error, reason } = await checkKeyWithOpenAI(KEY, { fetch, timeoutMs: 20 })
    assert.deepEqual({ ok, error, reason }, { ok: false, error: UNREACHABLE, reason: 'TimeoutError' })
  } finally {
    clearTimeout(hold)
  }
})

test('no message ever contains the key or a status code', () => {
  for (const message of [REJECTED, UNREACHABLE, CANT_CHECK]) {
    assert.ok(!message.includes(KEY))
    assert.doesNotMatch(message, /\d{3}/)
  }
})

test('every failure has a short reason for the log, without the key', async () => {
  const offline = async () => {
    throw new TypeError('fetch failed', { cause: Object.assign(new Error('getaddrinfo'), { code: 'ENOTFOUND' }) })
  }
  const cases = [
    [fakeFetch(401), 'status 401'],
    [fakeFetch(503), 'status 503'],
    [{ fetch: offline }, 'ENOTFOUND'],
  ]
  for (const [opts, reason] of cases) {
    const result = await checkKeyWithOpenAI(KEY, opts)
    assert.equal(result.reason, reason)
    assert.ok(!JSON.stringify(result).includes(KEY))
  }
})
