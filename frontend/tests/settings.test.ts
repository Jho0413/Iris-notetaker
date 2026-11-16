import assert from 'node:assert/strict'
import test from 'node:test'
import type { AppSettings } from '../src/api/client.ts'
import {
  afterSettingsRead,
  AUTO,
  BROWSER_KEY_NOTE,
  KEY_ACTION_FAILED,
  keyChanged,
  languageName,
  languageOptions,
  noKeyWarning,
  pendingLanguage,
  saveFailure,
  savedLanguage,
} from '../src/settings/settings.ts'

test('languages are named in English', () => {
  assert.equal(languageName('fr'), 'French')
  assert.equal(languageName('ja'), 'Japanese')
  assert.equal(languageName(AUTO), 'Detect automatically')
})

test('Whisper’s own code for Javanese is named', () => {
  assert.equal(languageName('jw'), 'Javanese')
})

test('a code the browser can’t name is shown as the code', () => {
  assert.equal(languageName('qq'), 'qq')
  assert.equal(languageName('not a tag'), 'not a tag')
})

test('options put detection first, sort by name and mark the default', () => {
  const options = languageOptions(['fr', 'en', 'de'], 'en')
  assert.deepEqual(options, [
    { value: 'auto', label: 'Detect automatically' },
    { value: 'en', label: 'English (default)' },
    { value: 'fr', label: 'French' },
    { value: 'de', label: 'German' },
  ])
})

test('detection can be the default', () => {
  assert.equal(languageOptions(['en'], AUTO)[0].label, 'Detect automatically (default)')
})

test('save failures say what happened and that nothing is lost', () => {
  assert.match(saveFailure(0), /isn't responding.*still here/)
  assert.match(saveFailure(409), /outside Iris/)
  assert.match(saveFailure(422), /language/)
  assert.match(saveFailure(500), /still here/)
  for (const status of [0, 409, 422, 500]) assert.doesNotMatch(saveFailure(status), /\d{3}|backend|error/i)
})

test('the saved confirmation reads naturally for a language and for detection', () => {
  assert.equal(savedLanguage('fr'), 'Saved. Sessions you start from now are transcribed in French.')
  assert.equal(savedLanguage(AUTO), 'Saved. Sessions you start from now detect the spoken language.')
})

test('a change is pending only when it differs from what is saved', () => {
  assert.equal(pendingLanguage(null, 'en'), null)
  assert.equal(pendingLanguage('en', 'en'), null) // changed back: nothing to save
  assert.equal(pendingLanguage('fr', 'en'), 'fr')
  assert.equal(pendingLanguage(AUTO, 'en'), AUTO)
  assert.equal(pendingLanguage('fr', undefined), null) // settings not loaded
})

test('the no-key warning fits where Iris runs', () => {
  const app = noKeyWarning(true)
  const browser = noKeyWarning(false)
  assert.equal(app.title, 'No OpenAI API key is set.')
  assert.equal(browser.title, app.title)
  assert.match(app.detail, /can’t make notes until you add one/)
  assert.doesNotMatch(app.detail, /\.env|make dev/) // app users never see developer files
  assert.match(browser.detail, /OPENAI_API_KEY.*backend\/\.env.*make dev/)
})

test('key changes are confirmed in words', () => {
  assert.equal(keyChanged('saved'), 'Key saved.')
  assert.equal(keyChanged('removed'), 'Key removed.')
  assert.doesNotMatch(KEY_ACTION_FAILED, /error|\d{3}/i)
})

test('the browser key note says where the key comes from', () => {
  assert.match(BROWSER_KEY_NOTE, /OPENAI_API_KEY.*backend\/\.env.*make dev/)
})

const read = { api_key_set: true } as AppSettings
const ready = { kind: 'ready', settings: read } as const

test('a read settles what is shown and starts the retries over', () => {
  assert.deepEqual(afterSettingsRead(read, { kind: 'loading' }, false, 3), { next: ready, retries: 0, retry: false })
})

test('a failed read while settings are shown keeps them and tries again', () => {
  assert.deepEqual(afterSettingsRead(undefined, ready, false, 0), { next: null, retries: 1, retry: true })
})

test('a failed read while settings are shown stops trying after five tries', () => {
  assert.deepEqual(afterSettingsRead(undefined, ready, false, 5), { next: null, retries: 5, retry: false })
})

test('a failed first read where it is retried keeps waiting and tries again', () => {
  assert.deepEqual(afterSettingsRead(undefined, { kind: 'loading' }, true, 0), { next: null, retries: 1, retry: true })
})

test('a failed first read where it is retried stops trying after five tries', () => {
  assert.deepEqual(afterSettingsRead(undefined, { kind: 'loading' }, true, 5), { next: null, retries: 5, retry: false })
})

test('a failed first read where it is not retried shows the error at once', () => {
  assert.deepEqual(afterSettingsRead(undefined, { kind: 'loading' }, false, 0), { next: { kind: 'error' }, retries: 0, retry: false })
  assert.deepEqual(afterSettingsRead(undefined, { kind: 'error' }, false, 0), { next: { kind: 'error' }, retries: 0, retry: false })
})
