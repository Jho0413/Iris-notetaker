// The Settings page's wording and language list. Pure, so it is testable under plain Node.
import { UNREACHABLE } from '../lib/messages.ts'

/** The language value that lets the transcription model detect the language itself. */
export const AUTO = 'auto'

const NAMES = new Intl.DisplayNames(['en'], { type: 'language' })

// Whisper's own codes that browsers don't name (Whisper says "jw" for Javanese; ISO says "jv").
const OVERRIDES: Record<string, string> = { jw: 'Javanese' }

/** A language's English name ("fr" → "French"), or the code itself if the browser can't name it. */
export function languageName(code: string): string {
  if (code === AUTO) return 'Detect automatically'
  if (OVERRIDES[code]) return OVERRIDES[code]
  try {
    const name = NAMES.of(code)
    return name && name !== code ? name : code
  } catch {
    return code // not a well-formed tag
  }
}

export interface LanguageOption {
  value: string
  label: string
}

/** "Detect automatically" first, then every language by name, the default marked. */
export function languageOptions(codes: readonly string[], defaultCode: string): LanguageOption[] {
  const mark = (code: string) => (code === defaultCode ? ' (default)' : '')
  const named = codes
    .map((code) => ({ value: code, label: languageName(code) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'en'))
    .map((o) => ({ ...o, label: o.label + mark(o.value) }))
  return [{ value: AUTO, label: languageName(AUTO) + mark(AUTO) }, ...named]
}

/** What the page says when saving fails, by the response status (0: Iris never answered). */
export function saveFailure(status: number): string {
  if (status === 0) return `${UNREACHABLE} Your changes are still here.`
  if (status === 409) {
    return 'This setting is now set outside Iris, so it can’t be changed here. Reload the page to see it.'
  }
  if (status === 422) return 'Iris can’t use that language. Choose another one.'
  return 'Iris couldn’t save your changes. Try again in a moment; they’re still here.'
}

/** The confirmation once the spoken language is saved. */
export function savedLanguage(value: string): string {
  const what = value === AUTO ? 'detect the spoken language' : `are transcribed in ${languageName(value)}`
  return `Saved. Sessions you start from now ${what}.`
}

/** The language chosen but not saved, or null when there is nothing to save. */
export function pendingLanguage(draft: string | null, saved: string | undefined): string | null {
  return draft !== null && saved !== undefined && draft !== saved ? draft : null
}

/** Whether moving from one page to another inside Iris should ask first (a change is unsaved). */
export function asksBeforeLeaving(pending: string | null, fromPath: string, toPath: string): boolean {
  return pending !== null && fromPath !== toPath
}

/** The no-key warning. In the desktop app the key is added in the app; in the browser the
 * engine reads it from its .env file at start-up, which a page can't change.
 * This module is the only UI code allowed developer setup words (tests/messages.test.ts): they
 * are shown only in the browser, which only developers use. */
export function noKeyWarning(inApp: boolean): { title: string; detail: string } {
  const title = 'No OpenAI API key is set.'
  return inApp
    ? { title, detail: 'Iris can’t make notes until you add one.' }
    : { title, detail: 'Add OPENAI_API_KEY to backend/.env and restart make dev.' }
}

/** The key row's note in the browser, where the engine reads the key at start-up. */
export const BROWSER_KEY_NOTE =
  'In the browser the key comes from OPENAI_API_KEY in backend/.env. Change it there and restart make dev.'

/** The confirmation after the key changed through the app (its engine restarted). */
export function keyChanged(action: 'saved' | 'removed'): string {
  return action === 'saved' ? 'Key saved.' : 'Key removed.'
}

/** When the app couldn't do what was asked (rare: the bridge call itself failed). */
export const KEY_ACTION_FAILED = 'Iris couldn’t change the key. Try again, or use Iris → Change OpenAI API Key… in the menu.'
