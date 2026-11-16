import { sessionFileUrl } from '../api/client'
import { formatClock } from '../lib/format'
import { PROVENANCE_LABELS } from '../lib/messages'
import { provenance, type FigureState, type Provenance } from '../lib/notes'
import { Icon } from '../components/Icon'

const BADGE_STYLES: Record<Provenance, string> = {
  verified: 'badge-verified',
  screenshot: 'badge-shot',
  unverified: 'badge-original',
}

export function ProvenanceBadge({ kind }: { kind: Provenance | undefined }) {
  if (!kind) return null
  return <span className={`badge ${BADGE_STYLES[kind]}`}>{PROVENANCE_LABELS[kind]}</span>
}

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
