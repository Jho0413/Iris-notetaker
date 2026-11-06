const assert = require('node:assert/strict')
const { test } = require('node:test')
const { refusedDebugSwitch } = require('../src/debugging.cjs')

const switches = (...names) => (name) => names.includes(name)

test('the packaged app refuses remote debugging', () => {
  assert.equal(refusedDebugSwitch({ packaged: true, hasSwitch: switches('remote-debugging-port') }), 'remote-debugging-port')
  assert.equal(refusedDebugSwitch({ packaged: true, hasSwitch: switches('remote-debugging-pipe') }), 'remote-debugging-pipe')
})

test('the packaged app starts without it', () => {
  assert.equal(refusedDebugSwitch({ packaged: true, hasSwitch: switches() }), null)
  assert.equal(refusedDebugSwitch({ packaged: true, hasSwitch: switches('enable-logging', 'remote-debugging-address') }), null)
})

test('development keeps remote debugging', () => {
  assert.equal(refusedDebugSwitch({ packaged: false, hasSwitch: switches('remote-debugging-port') }), null)
})
