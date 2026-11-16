import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { againLink, PROVENANCE_LABELS, saveRefused, UNREACHABLE } from '../src/lib/messages.ts'

test('an unreachable backend is described in Iris’s own words', () => {
  assert.equal(UNREACHABLE, "Iris isn't responding. Try again in a moment.")
})

test('a figure that fell back is labelled neutrally', () => {
  assert.equal(PROVENANCE_LABELS.unverified, 'Original image')
  assert.doesNotMatch(PROVENANCE_LABELS.unverified, /verif|fail|error|crop/i)
})

test('an input problem leads to making another recording the same way', () => {
  assert.deepEqual(againLink('upload'), { to: '/upload', label: 'Upload another recording' })
  assert.deepEqual(againLink('browser_live'), { to: '/live', label: 'Record again' })
})

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return sources(path)
    return /\.tsx?$/.test(name) && !name.endsWith('.d.ts') ? [path] : []
  })
}

test('no developer instructions or raw error details in the UI code', () => {
  const banned = [
    /make dev/, // how to start the backend
    /OPENAI_API_KEY|backend\/\.env/, // setup
    /Is (it|the backend) running/,
    /HTTP \$\{/, // status codes in messages
    /\.error\b.*\}<|\{(session|run)\.error/, // the old raw `error` field rendered
  ]
  // src/settings/settings.ts is the one exception: the browser-only API key wording, shown only
  // when Iris runs in a browser (developers), never in the desktop app (issue #21).
  const allowed = new URL('../src/settings/settings.ts', import.meta.url).pathname
  const files = sources(new URL('../src', import.meta.url).pathname).filter((path) => path !== allowed)
  const offenders = files.flatMap((path) => {
    const text = readFileSync(path, 'utf8')
    return banned.filter((re) => re.test(text)).map((re) => `${path}: ${re}`)
  })
  assert.deepEqual(offenders, [])
})

test('a refused save is told without Markdown line numbers', () => {
  assert.equal(
    saveRefused('Line 47: There\'s no figure "v9" in this session.'),
    'Your changes couldn’t be saved. There\'s no figure "v9" in this session.',
  )
})
