import assert from 'node:assert/strict'
import test from 'node:test'
import { nameToSave, renameFailure, TITLE_MAX_CHARS } from '../src/session/rename.ts'

test('a new name is trimmed and saved', () => {
  assert.equal(nameToSave('  Design review ', 'Live session'), 'Design review')
  assert.equal(nameToSave('第七講 🎉', 'Live session'), '第七講 🎉')
})

test('an empty or unchanged name keeps the old one', () => {
  assert.equal(nameToSave('', 'Live session'), null)
  assert.equal(nameToSave('   \n', 'Live session'), null)
  assert.equal(nameToSave(' Live session ', 'Live session'), null)
})

test('a name never goes past the engine’s limit, and never splits a character', () => {
  assert.equal(nameToSave('x'.repeat(TITLE_MAX_CHARS + 5), 'Old')?.length, TITLE_MAX_CHARS)
  const cut = nameToSave('x'.repeat(TITLE_MAX_CHARS - 1) + '🎉🎉', 'Old') ?? ''
  assert.equal(Array.from(cut).length, TITLE_MAX_CHARS)
  assert.ok(cut.endsWith('🎉'))
  assert.doesNotThrow(() => encodeURIComponent(cut)) // no lone half of an emoji
})

test('a failed rename is said in Iris’s own words', () => {
  assert.match(renameFailure(0), /isn't responding/)
  assert.match(renameFailure(404), /no longer exists/)
  assert.match(renameFailure(500), /couldn’t rename/)
  for (const status of [0, 404, 422, 500]) assert.doesNotMatch(renameFailure(status), /\d{3}|error/i)
})
