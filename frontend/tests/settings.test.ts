import assert from 'node:assert/strict'
import test from 'node:test'
import {
  AUTO,
  asksBeforeLeaving,
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

test('leaving asks only with a pending change and only for another page', () => {
  assert.equal(asksBeforeLeaving('fr', '/settings', '/'), true)
  assert.equal(asksBeforeLeaving('fr', '/settings', '/settings'), false) // same page (e.g. a hash)
  assert.equal(asksBeforeLeaving(null, '/settings', '/'), false)
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
