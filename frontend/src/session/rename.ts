import { UNREACHABLE } from '../lib/messages.ts'

export const TITLE_MAX_CHARS = 200

export function nameToSave(draft: string, current: string): string | null {
  const name = draft.trim()
  return name && name !== current ? Array.from(name).slice(0, TITLE_MAX_CHARS).join('') : null
}

export const RENAME_HINT = 'Enter to save · Esc to cancel'

export function renameFailure(status: number): string {
  if (status === 0) return UNREACHABLE
  if (status === 404) return 'This session no longer exists.'
  return 'Iris couldn’t rename it. Try again in a moment.'
}
