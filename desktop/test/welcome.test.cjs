const assert = require('node:assert/strict')
const { test } = require('node:test')
const { shouldWelcome } = require('../src/welcome.cjs')

test('the welcome shows only on a first launch with no key', () => {
  assert.equal(shouldWelcome({ hasKey: false, seen: false }), true)
  assert.equal(shouldWelcome({ hasKey: false, seen: true }), false) // "Set up later" earlier, or a removed key
  assert.equal(shouldWelcome({ hasKey: true, seen: false }), false) // an existing install with a key
  assert.equal(shouldWelcome({ hasKey: true, seen: true }), false)
})
