const assert = require('node:assert/strict')
const { test } = require('node:test')
const { unloadPrompt } = require('../src/unload.cjs')

test('the live page asks before stopping a recording', () => {
  for (const url of ['app://iris/live', 'app://iris/live/', 'app://iris/live?x=1']) {
    const prompt = unloadPrompt(url)
    assert.equal(prompt.message, 'Stop recording?', url)
    assert.equal(prompt.buttons[prompt.leave], 'Stop Recording')
    assert.equal(prompt.buttons[prompt.cancelId], 'Keep Recording')
  }
})

test('quitting doesn’t ask again about a recording: "Quit Iris?" already did', () => {
  assert.equal(unloadPrompt('app://iris/live', { quitting: true }), null)
})

test('an unsaved settings change never mentions recording', () => {
  const prompt = unloadPrompt('app://iris/settings')
  assert.equal(prompt.message, 'Leave without saving?')
  assert.doesNotMatch(`${prompt.message} ${prompt.detail} ${prompt.buttons}`, /record/i)
  assert.equal(prompt.buttons[prompt.cancelId], 'Keep Editing')
  assert.equal(prompt.defaultId, prompt.cancelId) // Return keeps the change
})

test('quitting with an unsaved change asks too', () => {
  const prompt = unloadPrompt('app://iris/settings', { quitting: true })
  assert.equal(prompt.message, 'Quit without saving?')
  assert.equal(prompt.buttons[prompt.leave], 'Quit')
})

test('a page path that isn’t live, or a bad URL, gets the neutral question', () => {
  for (const url of ['app://iris/', 'app://iris/sessions/live', 'app://iris/livex', 'not a url', '']) {
    assert.equal(unloadPrompt(url).message, 'Leave without saving?', url)
  }
})
