// Fixed user-facing wording shared by the pages (docs/frontend.md §3, errors). Pure, so it is
// testable under plain Node. Nothing here names the backend, a provider, a status code or a
// command: the user assumes Iris is set up, so every failure is said in Iris's own words.
import type { Session } from '../api/client'
import type { Provenance } from './state'

/** Any request that never reached Iris (it is down or restarting). */
export const UNREACHABLE = "Iris isn't responding. Try again in a moment."

/** Badges for how a figure was made. An original image is labelled neutrally, with no reason. */
export const PROVENANCE_LABELS: Record<Provenance, string> = {
  verified: 'Verified redraw',
  screenshot: 'Screenshot',
  unverified: 'Original image',
}

/** For a problem with the recording itself: make another one the way this one was made. */
export function againLink(source: Session['source']): { to: string; label: string } {
  return source === 'browser_live'
    ? { to: '/live', label: 'Record again' }
    : { to: '/upload', label: 'Upload another recording' }
}
