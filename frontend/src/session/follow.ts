/** How close to the bottom (px) a reader counts as "at the bottom" of a following list. */
export const BOTTOM_SLACK = 24

export interface ScrollMetrics {
  scrollHeight: number
  scrollTop: number
  clientHeight: number
}

/** The reader is at (or within `BOTTOM_SLACK` of) the bottom, so new lines may follow. */
export function isAtBottom(m: ScrollMetrics, slack = BOTTOM_SLACK): boolean {
  return m.scrollHeight - m.scrollTop - m.clientHeight < slack
}

/**
 * Where to scroll after new lines were added: to the new bottom if the reader was at the bottom
 * before they arrived, otherwise nowhere (null), so reading further up is never disturbed.
 */
export function followScrollTop(wasAtBottom: boolean, after: ScrollMetrics): number | null {
  return wasAtBottom ? Math.max(after.scrollHeight - after.clientHeight, 0) : null
}
