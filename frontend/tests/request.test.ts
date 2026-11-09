import assert from 'node:assert/strict'
import test from 'node:test'
import createClient from 'openapi-fetch'
import { act, engineAnswersOnly, engineDidNotAnswer } from '../src/session/request.ts'

/** A typed client like the app's, whose requests all get `answer` instead of reaching a server. */
function clientAnswering(answer: () => Promise<Response>) {
  const client = createClient({ baseUrl: 'http://iris.test', fetch: answer })
  client.use(engineAnswersOnly)
  return client
}

const reply = (status: number, body: string | null = '{}') => async () => new Response(body, { status })

test("what the dev proxy or the app's handler answers for a missing engine means no answer", () => {
  assert.equal(engineDidNotAnswer(502), true) // Vite's proxy, or the app's handler: no reply
  assert.equal(engineDidNotAnswer(503), true) // the app's handler: the engine isn't running
})

test("the engine's own answers, errors included, stay as they are", () => {
  for (const status of [200, 204, 400, 404, 409, 413, 415, 422, 500]) {
    assert.equal(engineDidNotAnswer(status), false, String(status))
  }
})

test('an action answered in place of the engine ends like one nothing answered', async () => {
  for (const status of [502, 503]) {
    const client = clientAnswering(reply(status, 'Bad Gateway'))
    assert.deepEqual(await act(client.POST('/api/sessions/s1/run' as never)), { ok: false, status: 0 })
  }
  const offline = clientAnswering(async () => {
    throw new TypeError('Failed to fetch')
  })
  assert.deepEqual(await act(offline.POST('/api/sessions/s1/run' as never)), { ok: false, status: 0 })
})

test("an action the engine answers keeps the engine's status", async () => {
  assert.deepEqual(await act(clientAnswering(reply(200)).POST('/api/x' as never)), { ok: true, status: 200 })
  assert.deepEqual(await act(clientAnswering(reply(409)).POST('/api/x' as never)), { ok: false, status: 409 })
  assert.deepEqual(await act(clientAnswering(reply(500)).POST('/api/x' as never)), { ok: false, status: 500 })
})

test('a load or save answered in place of the engine rejects, as a network failure does', async () => {
  // Pages that call the client directly catch this the same way they catch a network failure.
  await assert.rejects(clientAnswering(reply(503)).GET('/api/sessions' as never), /didn't answer/)
  const { error, response } = await clientAnswering(reply(404, '{"detail":"x"}')).GET('/api/x' as never)
  assert.ok(error)
  assert.equal(response.status, 404)
})
