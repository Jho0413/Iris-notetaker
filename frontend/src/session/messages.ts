// Fixed user-facing wording shared by the pages. Pure, so it is
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

/** Editing the notes: the save failures, the confirmations and the re-run banner. */
export const NOTES_EDIT = {
  saveWhileProcessing: 'Iris is making these notes again, so your changes can’t be saved yet. Save once it has finished.',
  saveRefused: 'Your changes couldn’t be saved.',
  saveFailed: 'Your changes couldn’t be saved. Try again.',
  discardTitle: 'Discard your changes?',
  discardBody: 'Your changes to the notes since the last save are lost.',
  leaveBody: 'Your changes to the notes haven’t been saved.',
  edited: 'Edited by you',
  restore: 'Restore generated notes',
  restoreTitle: 'Restore the generated notes?',
  restoreBody: 'Your edits are removed and the notes Iris made are shown again. This can’t be undone.',
  restoreFailed: 'The generated notes couldn’t be restored. Try again.',
  stale: 'Iris made these notes again after you edited them. You’re reading your edited version; the new notes are kept too.',
  useNew: 'Use the new notes',
  useNewTitle: 'Use the new notes?',
  useNewBody: 'Your edits are removed and the notes Iris made again are shown. This can’t be undone.',
  keep: 'Keep my edits',
  keepFailed: 'Your edits couldn’t be kept. Try again.',
  // A re-run can renumber or drop figures the edits place: the editor marks each one it can't find.
  keepInvalid: 'Your edits no longer fit the notes Iris made again: a figure they use may be gone. Edit the notes to fix what’s marked, then save.',
} as const

/** A save the engine refused, in the editor's words: Markdown line numbers mean nothing there. */
export function saveRefused(detail: string): string {
  return `${NOTES_EDIT.saveRefused} ${detail.replace(/^Line \d+: /, '')}`
}

/** Export PDF failed: the notes couldn't be loaded, or the desktop app couldn't save the file
 *  (the reason is in the console or the app's log). */
export const PDF_FAILED = 'The PDF couldn’t be saved. Try again.'
