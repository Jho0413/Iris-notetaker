import type { AppSettings } from '../api/client'
import { UNREACHABLE } from '../lib/messages.ts'

export type SettingsLoad = { kind: 'loading' } | { kind: 'error' } | { kind: 'ready'; settings: AppSettings }

export const SETTINGS_REREAD_MS = 2000
const SETTINGS_REREAD_TRIES = 5

export function afterSettingsRead(
  read: AppSettings | undefined,
  shown: SettingsLoad,
  retryFirstRead: boolean,
  retries: number,
): { next: SettingsLoad | null; retries: number; retry: boolean } {
  if (read) return { next: { kind: 'ready', settings: read }, retries: 0, retry: false }
  if (shown.kind !== 'ready' && !retryFirstRead) return { next: { kind: 'error' }, retries, retry: false }
  const retry = retries < SETTINGS_REREAD_TRIES
  return { next: null, retries: retry ? retries + 1 : retries, retry }
}

export const AUTO = 'auto'

const NAMES = new Intl.DisplayNames(['en'], { type: 'language' })

const OVERRIDES: Record<string, string> = { jw: 'Javanese' }

export function languageName(code: string): string {
  if (code === AUTO) return 'Detect automatically'
  if (OVERRIDES[code]) return OVERRIDES[code]
  try {
    const name = NAMES.of(code)
    return name && name !== code ? name : code
  } catch {
    return code
  }
}

export interface LanguageOption {
  value: string
  label: string
}

export function languageOptions(codes: readonly string[], defaultCode: string): LanguageOption[] {
  const mark = (code: string) => (code === defaultCode ? ' (default)' : '')
  const named = codes
    .map((code) => ({ value: code, label: languageName(code) }))
    .sort((a, b) => a.label.localeCompare(b.label, 'en'))
    .map((o) => ({ ...o, label: o.label + mark(o.value) }))
  return [{ value: AUTO, label: languageName(AUTO) + mark(AUTO) }, ...named]
}

export function saveFailure(status: number): string {
  if (status === 0) return `${UNREACHABLE} Your changes are still here.`
  if (status === 409) {
    return 'This setting is now set outside Iris, so it can’t be changed here. Reload the page to see it.'
  }
  if (status === 422) return 'Iris can’t use that language. Choose another one.'
  return 'Iris couldn’t save your changes. Try again in a moment; they’re still here.'
}

export function savedLanguage(value: string): string {
  const what = value === AUTO ? 'detect the spoken language' : `are transcribed in ${languageName(value)}`
  return `Saved. Sessions you start from now ${what}.`
}

export function pendingLanguage(draft: string | null, saved: string | undefined): string | null {
  return draft !== null && saved !== undefined && draft !== saved ? draft : null
}

export function noKeyWarning(inApp: boolean): { title: string; detail: string } {
  const title = 'No OpenAI API key is set.'
  return inApp
    ? { title, detail: 'Iris can’t make notes until you add one.' }
    : { title, detail: 'Add OPENAI_API_KEY to backend/.env and restart make dev.' }
}

export const BROWSER_KEY_NOTE =
  'In the browser the key comes from OPENAI_API_KEY in backend/.env. Change it there and restart make dev.'

export function keyChanged(action: 'saved' | 'removed'): string {
  return action === 'saved' ? 'Key saved.' : 'Key removed.'
}

export const ADD_KEY = 'Add key…'

export const KEY_ACTION_FAILED = 'Iris couldn’t change the key. Try again, or use Iris → Change OpenAI API Key… in the menu.'
