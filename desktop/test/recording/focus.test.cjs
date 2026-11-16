const assert = require('node:assert/strict')
const { test } = require('node:test')
const { windowNumber, bringSharedAppForward } = require('../../src/recording/focus.cjs')

function fakeExecFile({ stdout = 'activated', err = null } = {}) {
  const calls = []
  const execFile = (file, args, options, callback) => {
    calls.push({ file, args, options })
    setImmediate(() => callback(err, stdout, ''))
  }
  return { execFile, calls }
}

test('a window source gives its window number; anything else none', () => {
  assert.equal(windowNumber('window:210373:0'), '210373')
  assert.equal(windowNumber('window:5:1'), '5')
  assert.equal(windowNumber('screen:1:0'), null)
  assert.equal(windowNumber('window:abc:0'), null)
  assert.equal(windowNumber('window:5:0; rm -rf ~'), null)
  assert.equal(windowNumber(undefined), null)
})

test('a shared window activates its app, the number passed as an argument', async () => {
  const { execFile, calls } = fakeExecFile()
  const logs = []
  await bringSharedAppForward('window:210373:0', { execFile, log: (line) => logs.push(line) })
  assert.equal(calls.length, 1)
  assert.equal(calls[0].file, '/usr/bin/osascript')
  assert.deepEqual(calls[0].args.slice(0, 3), ['-l', 'JavaScript', '-e'])
  assert.equal(calls[0].args[4], '210373')
  assert.ok(!calls[0].args[3].includes('210373'), 'the number is never spliced into the script')
  assert.ok(calls[0].options.timeout > 0)
  assert.deepEqual(logs, [])
})

test('a shared screen changes nothing', async () => {
  const { execFile, calls } = fakeExecFile()
  await bringSharedAppForward('screen:1:0', { execFile, log: () => assert.fail('nothing to log') })
  assert.equal(calls.length, 0)
})

test('a failure is logged, never thrown: the share goes on', async () => {
  for (const [fake, expected] of [
    [fakeExecFile({ err: new Error('timed out') }), /couldn't bring .* timed out/],
    [fakeExecFile({ stdout: 'no window\n' }), /didn't bring .*: no window$/],
    [fakeExecFile({ stdout: 'refused' }), /didn't bring .*: refused$/],
  ]) {
    const logs = []
    await bringSharedAppForward('window:7:0', { execFile: fake.execFile, log: (line) => logs.push(line) })
    assert.equal(logs.length, 1)
    assert.match(logs[0], expected)
  }
})
