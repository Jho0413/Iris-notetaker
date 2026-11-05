const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const { test } = require('node:test')
const { createAppState } = require('../src/appstate.cjs')

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'iris-state-'))
}

test('flags are remembered across launches, in a folder that may not exist yet', () => {
  const file = path.join(tmpDir(), 'Iris', 'app-state.json')
  assert.equal(createAppState(file).get('welcomeShown'), undefined)
  assert.equal(createAppState(file).set('welcomeShown', true), true)
  assert.equal(createAppState(file).get('welcomeShown'), true) // a later launch
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { welcomeShown: true })
  assert.ok(!fs.existsSync(`${file}.tmp`))
})

test('setting one flag keeps the others', () => {
  const file = path.join(tmpDir(), 'app-state.json')
  fs.writeFileSync(file, JSON.stringify({ fromANewerIris: 'kept' }))
  createAppState(file).set('welcomeShown', true)
  assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { fromANewerIris: 'kept', welcomeShown: true })
})

test('a missing or damaged file reads as no flags, and the next write replaces it', () => {
  for (const content of ['{oops', '', '[1, 2]', 'null', '"text"']) {
    const file = path.join(tmpDir(), 'app-state.json')
    fs.writeFileSync(file, content)
    const state = createAppState(file)
    assert.equal(state.get('welcomeShown'), undefined, content)
    assert.equal(state.set('welcomeShown', true), true)
    assert.equal(state.get('welcomeShown'), true)
  }
})

test('a file that can’t be written is reported, not thrown', () => {
  const blocker = path.join(tmpDir(), 'not-a-folder')
  fs.writeFileSync(blocker, '')
  const state = createAppState(path.join(blocker, 'app-state.json'))
  assert.equal(state.set('welcomeShown', true), false)
  assert.equal(state.get('welcomeShown'), undefined)
})
