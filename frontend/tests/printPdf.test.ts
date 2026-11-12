import assert from 'node:assert/strict'
import test from 'node:test'
import { browserPrint, runExport, type ExportSteps } from '../src/session/printPdf.ts'
import { shownTitle } from '../src/session/format.ts'

const SHEET = { isConnected: true } as HTMLElement

/** Steps that record what ran, with any of them replaced. */
function steps(over: Partial<ExportSteps<string>> = {}) {
  const ran: string[] = []
  const all: ExportSteps<string> = {
    load: async () => { ran.push('load'); return 'notes' },
    show: () => { ran.push('show'); return SHEET },
    ready: async () => { ran.push('ready') },
    print: async () => { ran.push('print') },
    hide: () => { ran.push('hide') },
    ...over,
  }
  return { ran, all }
}

// Failures are logged on purpose; keep the test output clean.
test.beforeEach(() => { console.error = () => {} })

test('an export loads, draws, waits, prints, then removes the sheet', async () => {
  const { ran, all } = steps()
  assert.equal(await runExport(all), true)
  assert.deepEqual(ran, ['load', 'show', 'ready', 'print', 'hide'])
})

test('notes that cannot be loaded: nothing is drawn or printed, and it says so', async () => {
  const { ran, all } = steps({ load: async () => { ran.push('load'); return undefined } })
  assert.equal(await runExport(all), false)
  assert.deepEqual(ran, ['load', 'hide'])
})

test('no print sheet (the page closed meanwhile): the page underneath is never printed', async () => {
  const { ran, all } = steps({ show: () => { ran.push('show'); return null } })
  assert.equal(await runExport(all), false)
  assert.ok(!ran.includes('print'))
  assert.equal(ran.at(-1), 'hide')
})

test('leaving the page while the figures load: the page underneath is never printed', async () => {
  const sheet = { isConnected: true } as { isConnected: boolean }
  const { ran, all } = steps({
    show: () => { ran.push('show'); return sheet as HTMLElement },
    ready: async () => { ran.push('ready'); sheet.isConnected = false },
  })
  assert.equal(await runExport(all), false)
  assert.deepEqual(ran, ['load', 'show', 'ready', 'hide'])
  assert.ok(!ran.includes('print'))
  assert.equal(ran.at(-1), 'hide')
})

test('a save that fails, or a load that throws, still removes the sheet', async () => {
  const failingSave = steps({ print: async () => { throw new Error('disk full') } })
  assert.equal(await runExport(failingSave.all), false)
  assert.equal(failingSave.ran.at(-1), 'hide')
  const offline = steps({ load: async () => { throw new TypeError('fetch failed') } })
  assert.equal(await runExport(offline.all), false)
  assert.deepEqual(offline.ran, ['hide'])
})

test('the sheet stays until the print is done with it', async () => {
  let release!: () => void
  const { ran, all } = steps({ print: () => new Promise<void>((r) => { release = r }) })
  const done = runExport(all)
  await new Promise((r) => setTimeout(r, 0))
  assert.ok(!ran.includes('hide')) // still printing: the sheet must be there
  release()
  assert.equal(await done, true)
  assert.equal(ran.at(-1), 'hide')
})

/** A window whose print() returns at once and fires afterprint later, as some browsers do. */
function fakeWindow() {
  const listeners = new Set<() => void>()
  return {
    printed: 0,
    print() { this.printed += 1 },
    addEventListener: (_: string, fn: () => void) => listeners.add(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    afterprint: () => [...listeners].forEach((fn) => fn()),
    listeners,
  }
}

test('the browser print resolves on afterprint, not when print() returns', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const win = fakeWindow()
  let resolved = false
  const fallbackMs = 1000
  const printing = browserPrint(win as unknown as Window, fallbackMs).then(() => { resolved = true })
  assert.equal(win.printed, 1)
  t.mock.timers.tick(fallbackMs - 1) // just short of the fallback
  await new Promise((r) => setImmediate(r)) // setImmediate isn't faked
  assert.equal(resolved, false)
  win.afterprint()
  await printing
  assert.equal(win.listeners.size, 0) // its listener is removed
})

test('a browser that never fires afterprint still finishes, after the fallback', async () => {
  const win = fakeWindow()
  await browserPrint(win as unknown as Window, 10)
  assert.equal(win.listeners.size, 0)
})

test('an untitled session is shown as one', () => {
  assert.equal(shownTitle(''), 'Untitled session')
  assert.equal(shownTitle('Algebra'), 'Algebra')
})
