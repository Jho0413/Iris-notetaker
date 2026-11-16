// Renaming a session in place. Pure, so it is testable under plain Node.
import { UNREACHABLE } from '../lib/messages.ts'

/** Longest session name, in characters: the engine's limit (TITLE_MAX_CHARS in
 * backend/src/iris/api/routes/sessions.py), so the field stops where the API would refuse. */
export const TITLE_MAX_CHARS = 200

/** The name to save, or null for nothing to save: an empty or unchanged name keeps the old one. */
export function nameToSave(draft: string, current: string): string | null {
  const name = draft.trim()
  // Cut by characters, not UTF-16 units, so an emoji at the limit is never split in half.
  return name && name !== current ? Array.from(name).slice(0, TITLE_MAX_CHARS).join('') : null
}

/** Shown under the field while editing. */
export const RENAME_HINT = 'Enter to save · Esc to cancel'

/** When the rename didn't save (0: Iris never answered). The text stays in the field. */
export function renameFailure(status: number): string {
  if (status === 0) return UNREACHABLE
  if (status === 404) return 'This session no longer exists.'
  return 'Iris couldn’t rename it. Try again in a moment.'
}
