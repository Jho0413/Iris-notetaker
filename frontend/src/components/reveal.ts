// How far a sideways-scrolling row (the tabs on a phone) must scroll so one item in it is wholly
// in view. Pure, so plain `node --test` covers it; Tabs.tsx measures and applies it.

/** A horizontal span on screen, in CSS pixels. */
export interface Span {
  left: number
  right: number
}

/**
 * The change to the row's scrollLeft that brings `item` wholly into `view`: negative to scroll
 * back, positive to scroll on, 0 if it is already in view. An item wider than the view lines up
 * with the view's start, so its beginning (the label) shows.
 */
export function revealDelta(item: Span, view: Span): number {
  if (item.left < view.left || item.right - item.left > view.right - view.left) return item.left - view.left
  if (item.right > view.right) return item.right - view.right
  return 0
}
