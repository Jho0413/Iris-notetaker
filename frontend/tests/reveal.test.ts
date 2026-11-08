import assert from 'node:assert/strict'
import test from 'node:test'
import { revealDelta } from '../src/components/reveal.ts'

const view = { left: 16, right: 231 }

test('an item already in view needs no scroll', () => {
  assert.equal(revealDelta({ left: 16, right: 60 }, view), 0)
  assert.equal(revealDelta({ left: 100, right: 231 }, view), 0)
})

test('an item cut off at the end scrolls on just enough to show it', () => {
  // The Figures tab at 320px with long counts: 187–270 in a row that ends at 231.
  assert.equal(revealDelta({ left: 187, right: 270 }, view), 39)
})

test('an item cut off at the start scrolls back to its start', () => {
  assert.equal(revealDelta({ left: -20, right: 30 }, view), -36)
})

test('an item wider than the row lines up with its start', () => {
  assert.equal(revealDelta({ left: 100, right: 400 }, view), 84)
  assert.equal(revealDelta({ left: -50, right: 300 }, view), -66)
})
