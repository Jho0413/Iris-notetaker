import type { Target } from './blocks.ts'

export interface Measured {
  pos: number
  top: number
  bottom: number
  body?: { left: number; right: number; bottom: number; children: Measured[] }
}

export interface Spot {
  target: Target
  y: number
  left: number
  right: number
  depth: 0 | 1
}

const GAP = 6

function between(list: Measured[], at: number, fallback: number): number {
  const prev = list[at - 1]
  const next = list[at]
  if (prev && next) return (prev.bottom + next.top) / 2
  if (next) return next.top - GAP
  if (prev) return prev.bottom + GAP
  return fallback
}

export function landingSpots(
  blocks: Measured[],
  column: { left: number; right: number },
  allowed: (target: Target) => boolean,
): Spot[] {
  const spots: Spot[] = []
  for (let i = 0; i <= blocks.length; i++) {
    const target = { parent: null, index: i }
    if (allowed(target)) spots.push({ target, y: between(blocks, i, 0), left: column.left, right: column.right, depth: 0 })
  }
  for (const block of blocks) {
    if (!block.body) continue
    const { children, left, right, bottom } = block.body
    for (let i = 0; i <= children.length; i++) {
      const target = { parent: block.pos, index: i + 1 }
      if (allowed(target)) spots.push({ target, y: between(children, i, bottom), left, right, depth: 1 })
    }
  }
  return spots
}

const OFF = 10_000
const OUTDENT = 60
const INSIDE_BONUS = 4

export function nearestSpot(spots: readonly Spot[], x: number, y: number): Spot | null {
  let best: Spot | null = null
  let bestScore = OFF
  for (const spot of spots) {
    const beside = x >= spot.left - (spot.depth ? GAP - 2 : OUTDENT) && x <= spot.right + 40
    const score = Math.abs(y - spot.y) + (beside ? 0 : OFF) - spot.depth * INSIDE_BONUS
    if (score < bestScore) {
      best = spot
      bestScore = score
    }
  }
  return best
}
