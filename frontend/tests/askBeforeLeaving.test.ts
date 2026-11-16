import assert from 'node:assert/strict'
import test from 'node:test'
import { asksBeforeLeaving } from '../src/hooks/useAskBeforeLeaving.ts'

test('leaving asks only with unsaved work and only for another page', () => {
  assert.equal(asksBeforeLeaving(true, '/settings', '/'), true)
  assert.equal(asksBeforeLeaving(true, '/live', '/live'), false)
  assert.equal(asksBeforeLeaving(false, '/settings', '/'), false)
})
