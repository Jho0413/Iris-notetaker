// Where a dragged block can land, measured once when the drag starts, so moving the pointer only
// picks the nearest spot (cheap enough for every frame). Pure: the editor measures the page and
// passes plain numbers in page coordinates.
import type { Target } from './blocks.ts'

export interface Measured {
  pos: number
  top: number
  bottom: number
  /** A box or step: where its blocks are (after its title), with their own measures. */
  body?: { left: number; right: number; bottom: number; children: Measured[] }
}

export interface Spot {
  target: Target
  /** Where the drop line is drawn. */
  y: number
  left: number
  right: number
  /** 1 inside a box or step: the line is indented to its text. */
  depth: 0 | 1
}

const GAP = 6 // the line sits this far outside the first and last block

function between(list: Measured[], at: number, fallback: number): number {
  const prev = list[at - 1]
  const next = list[at]
  if (prev && next) return (prev.bottom + next.top) / 2
  if (next) return next.top - GAP
  if (prev) return prev.bottom + GAP
  return fallback
}

/** Every spot a block may land: between top-level blocks, and between the blocks inside each
 *  box or step; `allowed` says which (no step inside a step, no third level, not into itself). */
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
      // Child 0 of a box or step is its title, so its blocks are children 1 and on.
      const target = { parent: block.pos, index: i + 1 }
      if (allowed(target)) spots.push({ target, y: between(children, i, bottom), left, right, depth: 1 })
    }
  }
  return spots
}

const OFF = 10_000 // a spot the pointer is not beside
const OUTDENT = 60 // a top-level spot reaches this far left of the column
const INSIDE_BONUS = 4 // between equally near spots, inside a block wins

/** The spot nearest the pointer. A spot inside a box or step counts only while the pointer is
 *  over that block's text column, so moving left of it leaves the block. */
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
