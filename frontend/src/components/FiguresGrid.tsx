import { sessionFileUrl } from '../api/client'
import { formatClock, provenance, type FigureState, type Provenance } from '../session/state'
import { Icon } from './Icon'

/** The badge for a figure's provenance; shared by the Figures tab and the notes. */
export function ProvenanceBadge({ kind }: { kind: Provenance | undefined }) {
  if (kind === 'unverified') return <span className="badge badge-unverified">Original crop · not verified</span>
  if (kind === 'screenshot') return <span className="badge badge-shot">Screenshot</span>
  if (kind === 'verified') return <span className="badge badge-verified">Verified redraw</span>
  return null
}

/** Every figure selected, each in a fixed 4:3 box so nothing moves when images arrive. */
export function FiguresGrid({ sessionId, figures }: { sessionId: string; figures: FigureState[] }) {
  return (
    <ul className="figures" role="list">
      {figures.map((f) => (
        <li key={f.id} id={`figure-${f.id}`}>
          <figure className="figure-card">
            {f.rendered && f.path ? (
              <a
                className="figure-box"
                href={sessionFileUrl(sessionId, f.path)}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${f.caption || 'figure'} full size`}
              >
                <img src={sessionFileUrl(sessionId, f.path)} alt={f.caption} loading="lazy" />
              </a>
            ) : (
              <div className="figure-box pending">
                <Icon name="spinner" />
                Redrawing…
              </div>
            )}
            <figcaption>
              <span className="cap">{f.caption || 'Untitled figure'}</span>
              {f.t !== undefined && <span className="num">Shown at {formatClock(f.t)}</span>}
              <ProvenanceBadge kind={provenance(f)} />
            </figcaption>
          </figure>
        </li>
      ))}
    </ul>
  )
}
