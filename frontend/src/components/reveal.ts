export interface Span {
  left: number
  right: number
}

export function revealDelta(item: Span, view: Span): number {
  if (item.left < view.left || item.right - item.left > view.right - view.left) return item.left - view.left
  if (item.right > view.right) return item.right - view.right
  return 0
}
