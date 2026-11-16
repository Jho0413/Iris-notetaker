import assert from 'node:assert/strict'
import { test } from 'node:test'
import { landingSpots, nearestSpot, type Measured } from '../src/notes/editor/landing.ts'

const BLOCKS: Measured[] = [
  { pos: 0, top: 100, bottom: 140 },
  {
    pos: 10,
    top: 160,
    bottom: 360,
    body: { left: 136, right: 700, bottom: 360, children: [{ pos: 20, top: 200, bottom: 260 }, { pos: 40, top: 280, bottom: 340 }] },
  },
  { pos: 60, top: 380, bottom: 420 },
]
const COLUMN = { left: 100, right: 700 }

test('spots between top-level blocks and inside each box or step', () => {
  const spots = landingSpots(BLOCKS, COLUMN, () => true)
  assert.deepEqual(
    spots.map((s) => [s.target.parent, s.target.index, s.y, s.depth]),
    [
      [null, 0, 94, 0], [null, 1, 150, 0], [null, 2, 370, 0], [null, 3, 426, 0],
      [10, 1, 194, 1], [10, 2, 270, 1], [10, 3, 346, 1],
    ],
  )
})

test('spots that are not allowed are left out', () => {
  const spots = landingSpots(BLOCKS, COLUMN, (t) => t.parent === null)
  assert.ok(spots.every((s) => s.depth === 0))
})

test('the pointer picks the nearest spot; inside a block only over its text column', () => {
  const spots = landingSpots(BLOCKS, COLUMN, () => true)
  const at = (x: number, y: number) => {
    const s = nearestSpot(spots, x, y)
    return s && [s.target.parent, s.target.index]
  }
  assert.deepEqual(at(300, 268), [10, 2])
  assert.deepEqual(at(110, 352), [null, 2])
  assert.deepEqual(at(300, 352), [10, 3])
  assert.deepEqual(at(60, 95), [null, 0])
  assert.equal(at(5, 95), null)
})

test('an empty layout has one spot at the top', () => {
  assert.deepEqual(landingSpots([], COLUMN, () => true).map((s) => s.target), [{ parent: null, index: 0 }])
})

test('picking a spot looks at each spot once, so it stays cheap for long notes', () => {
  const many: Measured[] = Array.from({ length: 400 }, (_, i) => ({
    pos: i * 10,
    top: i * 50,
    bottom: i * 50 + 40,
    body: i % 4 ? undefined : { left: 136, right: 700, bottom: i * 50 + 40, children: [{ pos: i * 10 + 1, top: i * 50 + 5, bottom: i * 50 + 35 }] },
  }))
  const spots = landingSpots(many, COLUMN, () => true)
  const looks = spots.map(() => 0)
  const counted = new Proxy(spots, {
    get(target, key, receiver) {
      if (typeof key === 'string' && /^\d+$/.test(key)) looks[Number(key)]++
      return Reflect.get(target, key, receiver)
    },
  })
  assert.ok(nearestSpot(counted, 300, 12_345))
  assert.deepEqual(looks, spots.map(() => 1))
})
