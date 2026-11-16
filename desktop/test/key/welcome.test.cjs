const assert = require('node:assert/strict')
const { test } = require('node:test')
const { shouldWelcome } = require('../../src/key/welcome.cjs')

test('the welcome shows only on a first launch with no key', () => {
  assert.equal(shouldWelcome({ hasKey: false, seen: false }), true)
  assert.equal(shouldWelcome({ hasKey: false, seen: true }), false)
  assert.equal(shouldWelcome({ hasKey: true, seen: false }), false)
  assert.equal(shouldWelcome({ hasKey: true, seen: true }), false)
})
