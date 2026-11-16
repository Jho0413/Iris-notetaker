import type { Notes } from '../api/client'

export interface FigureState {
  id: string
  caption: string
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

export function notesFigures(notes: Notes | null): FigureState[] {
  return (notes?.visuals ?? []).map((v) => ({
    id: v.id,
    caption: v.caption,
    t: v.frame.t,
    path: v.path,
    renderAs: v.render_as,
    verified: v.verified ?? (v.render_as === 'screenshot' ? undefined : true),
    rendered: true,
  }))
}

export function provenance(figure: FigureState): Provenance | undefined {
  if (!figure.rendered || figure.verified === undefined) return undefined
  if (!figure.verified) return 'unverified'
  return figure.renderAs === 'screenshot' ? 'screenshot' : 'verified'
}

export function figureMeta(figures: readonly FigureState[]): Map<string, FigureMeta> {
  return new Map(figures.map((f) => [f.id, { t: f.t, provenance: provenance(f) }]))
}
