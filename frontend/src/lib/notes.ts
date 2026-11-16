import type { Notes } from '../api/client'

export interface FigureState {
  id: string
  caption: string
  /** Seconds into the session where the figure was shown. */
  t?: number
  path?: string
  renderAs?: string
  verified?: boolean
  rendered: boolean
}

export type Provenance = 'verified' | 'screenshot' | 'unverified'

export interface FigureMeta {
  t?: number
  provenance?: Provenance
}

export interface OutlineItem {
  id: string
  text: string
}

/** The figures the finished notes place, in their order. */
export function notesFigures(notes: Notes | null): FigureState[] {
  return (notes?.visuals ?? []).map((v) => ({
    id: v.id,
    caption: v.caption,
    t: v.frame.t,
    path: v.path,
    renderAs: v.render_as,
    // The notes say how each figure was made. Notes from before they did: a redraw only
    // reaches them after passing its check, and a screenshot may be requested or a fallback.
    verified: v.verified ?? (v.render_as === 'screenshot' ? undefined : true),
    rendered: true,
  }))
}

/**
 * How a figure was produced, when it is known. `unverified` is the
 * original image, shown because no redraw could be used (labelled neutrally, with no reason). In
 * notes from before figures recorded this, a screenshot may be either, so neither is claimed.
 */
export function provenance(figure: FigureState): Provenance | undefined {
  if (!figure.rendered || figure.verified === undefined) return undefined
  if (!figure.verified) return 'unverified'
  return figure.renderAs === 'screenshot' ? 'screenshot' : 'verified'
}

/** Per figure id: when it was shown and how it was made (the figure's caption line). */
export function figureMeta(figures: readonly FigureState[]): Map<string, FigureMeta> {
  return new Map(figures.map((f) => [f.id, { t: f.t, provenance: provenance(f) }]))
}
