import type { Session } from '../api/client'
import type { Provenance } from './notes'

export const UNREACHABLE = "Iris isn't responding. Try again in a moment."

export const RETRY = 'Retry'

export const KEEP_EDITING = 'Keep editing'

export const BACK_TO_LIBRARY = 'Back to the library'

export function actionFailure(status: number, words: string): string {
  return status === 0 ? UNREACHABLE : words
}

export const PROVENANCE_LABELS: Record<Provenance, string> = {
  verified: 'Verified redraw',
  screenshot: 'Screenshot',
  unverified: 'Original image',
}

export function againLink(source: Session['source']): { to: string; label: string } {
  return source === 'browser_live'
    ? { to: '/live', label: 'Record again' }
    : { to: '/upload', label: 'Upload another recording' }
}

export const DELETE_SESSION = {
  title: (title: string) => `Delete "${title}"?`,
  body: "This removes the recording, transcript, figures and notes from this computer. It can't be undone.",
  confirm: 'Delete session',
}

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
  keepInvalid: 'Your edits no longer fit the notes Iris made again: a figure they use may be gone. Edit the notes to fix what’s marked, then save.',
} as const

export function saveRefused(detail: string): string {
  return `${NOTES_EDIT.saveRefused} ${detail.replace(/^Line \d+: /, '')}`
}

export const PDF_FAILED = 'The PDF couldn’t be saved. Try again.'

export const UPLOAD_INTERRUPTED = "The upload was interrupted. Iris isn't responding; try again in a moment."

export const UPLOAD_REFUSED = "Iris couldn't accept this recording. Try again in a moment."

export function detailsLabel(open: boolean): string {
  return open ? 'Hide details' : 'Details'
}
