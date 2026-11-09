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
  assert.equal(createAppState(file).set('welcomeShown', true), null)
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
    assert.equal(state.set('welcomeShown', true), null)
    assert.equal(state.get('welcomeShown'), true)
  }
})

test('a file that can’t be written returns the error, not thrown, so the caller can log why', () => {
  const blocker = path.join(tmpDir(), 'not-a-folder')
  fs.writeFileSync(blocker, '')
  const state = createAppState(path.join(blocker, 'app-state.json'))
  const err = state.set('welcomeShown', true)
  assert.ok(err instanceof Error)
  assert.match(err.message, /EEXIST|ENOTDIR/)
  assert.equal(state.get('welcomeShown'), undefined)
})

test('a read-only folder returns the error and keeps the flags already there', (t) => {
  if (process.getuid?.() === 0) return t.skip('root writes to read-only folders')
  const dir = tmpDir()
  const file = path.join(dir, 'app-state.json')
  fs.writeFileSync(file, JSON.stringify({ welcomeShown: false }))
  fs.chmodSync(dir, 0o500)
  t.after(() => fs.chmodSync(dir, 0o700))
  const state = createAppState(file)
  const err = state.set('welcomeShown', true)
  assert.equal(err?.code, 'EACCES')
  assert.ok(err.message.includes(dir), err.message) // says where, for the log
  assert.equal(state.get('welcomeShown'), false)
})
