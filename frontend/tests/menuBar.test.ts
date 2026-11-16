import assert from 'node:assert/strict'
import test from 'node:test'
import { menuBarPhase, menuBarReporter } from '../src/capture/menuBar.ts'

test('menuBarPhase: a left page still uploading keeps an idle page "stopping"', () => {
  assert.equal(menuBarPhase('idle', 0), 'idle')
  assert.equal(menuBarPhase('idle', 1), 'stopping')
  assert.equal(menuBarPhase('recording', 1), 'recording')
  assert.equal(menuBarPhase('requesting', 2), 'requesting')
})

function setup() {
  const sent: string[] = []
  const reporter = menuBarReporter((phase) => sent.push(phase))
  let settle = () => {}
  const stop = new Promise<void>((_, reject) => (settle = () => reject(new Error('still unreachable'))))
  return { sent, reporter, stop, settle: async () => (settle(), await new Promise((r) => setTimeout(r))) }
}

test('a page left after a failed stop: "stopping" until its upload settles, then idle', async () => {
  const t = setup()
  t.reporter.phase('unfinished', 0)
  t.reporter.left(t.stop)
  assert.equal(t.sent.at(-1), 'stopping')
  await t.settle()
  assert.equal(t.sent.at(-1), 'idle')
})

test('the left page settling late never overwrites a newer recording', async () => {
  const t = setup()
  t.reporter.phase('unfinished', 0)
  t.reporter.left(t.stop)
  t.reporter.phase('idle', 0)
  assert.equal(t.sent.at(-1), 'stopping')
  t.reporter.phase('requesting', 0)
  t.reporter.phase('recording', 1_800_000_000_000)
  await t.settle()
  assert.equal(t.sent.at(-1), 'recording')
})

test('a page left with nothing to upload reports idle at once', () => {
  const t = setup()
  t.reporter.phase('idle', 0)
  t.reporter.left(undefined)
  assert.deepEqual(t.sent, ['idle', 'idle'])
})

test("the Mac's sound refused and the screen shared are told with the recording, and cleared when the page goes", () => {
  const sent: unknown[][] = []
  const reporter = menuBarReporter((...report) => sent.push(report))
  reporter.phase('recording', 1_800_000_000_000, true, true)
  reporter.left(undefined)
  assert.deepEqual(sent, [
    ['recording', 1_800_000_000_000, true, true],
    ['idle', 0, false, false],
  ])
})
