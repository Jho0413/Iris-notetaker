const assert = require('node:assert/strict')
const { test } = require('node:test')
const { listSources, defaultSelection, chosenId, soundOnly } = require('../src/picker.cjs')

const screen = (id, displayId) => ({ id: `screen:${id}:0`, name: 'Entire screen', displayId, hasThumbnail: true })
const win = (id, name, hasThumbnail = true) => ({ id: `window:${id}:0`, name, displayId: '', hasThumbnail })
const ONE_DISPLAY = { ownWindowIds: [], displays: [{ id: 1, label: 'Built-in Retina Display' }], primaryDisplayId: 1 }

test('one display: "Entire screen", recommended, before the windows', () => {
  const list = listSources([win(7, 'Lecture 4'), screen(1, '1'), win(8, 'Moodle')], ONE_DISPLAY)
  assert.deepEqual(list.screens, [{ id: 'screen:1:0', label: 'Entire screen', detail: '', recommended: true }])
  assert.deepEqual(list.windows, [
    { id: 'window:7:0', title: 'Lecture 4' },
    { id: 'window:8:0', title: 'Moodle' },
  ])
  assert.equal(defaultSelection(list), 'screen:1:0')
})

test('several displays: the main display first, numbered, with each display name', () => {
  const context = {
    ownWindowIds: [],
    displays: [
      { id: 1, label: 'Built-in Retina Display' },
      { id: 5, label: 'DELL U2723QE' },
    ],
    primaryDisplayId: 5, // the external display is the main one
  }
  const list = listSources([screen(1, '1'), screen(5, '5')], context)
  assert.deepEqual(list.screens, [
    { id: 'screen:5:0', label: 'Screen 1', detail: 'DELL U2723QE', recommended: true },
    { id: 'screen:1:0', label: 'Screen 2', detail: 'Built-in Retina Display', recommended: false },
  ])
  assert.equal(defaultSelection(list), 'screen:5:0')
})

test('a screen whose display is unknown keeps its place, with no name', () => {
  const list = listSources([screen(1, '1'), screen(9, '')], ONE_DISPLAY)
  assert.deepEqual(
    list.screens.map((s) => [s.id, s.label, s.detail]),
    [
      ['screen:1:0', 'Screen 1', 'Built-in Retina Display'],
      ['screen:9:0', 'Screen 2', ''],
    ],
  )
})

test("Iris's own windows (the picker too) are never offered", () => {
  const context = { ...ONE_DISPLAY, ownWindowIds: ['window:100:0', 'window:101:0'] }
  const list = listSources([screen(1, '1'), win(100, 'Iris'), win(101, 'Record with Iris'), win(7, 'Keynote')], context)
  assert.deepEqual(list.windows, [{ id: 'window:7:0', title: 'Keynote' }])
})

test('untitled and empty windows are left out; titles are trimmed', () => {
  const list = listSources([win(1, ''), win(2, '   '), win(3, 'Blank', false), win(4, '  Notes  ')], ONE_DISPLAY)
  assert.deepEqual(list.windows, [{ id: 'window:4:0', title: 'Notes' }])
})

test('non-ASCII window titles are kept as they are', () => {
  const list = listSources([win(1, 'Cours 4 – Graphes 📊 図')], ONE_DISPLAY)
  assert.equal(list.windows[0].title, 'Cours 4 – Graphes 📊 図')
})

test('the default: the first screen, else the first window, else nothing', () => {
  assert.equal(defaultSelection(listSources([win(7, 'Keynote')], ONE_DISPLAY)), 'window:7:0')
  assert.equal(defaultSelection(listSources([], ONE_DISPLAY)), null)
})

test('the chosen id must be one the picker offered', () => {
  const list = listSources([screen(1, '1'), win(7, 'Keynote'), win(100, 'Iris')], { ...ONE_DISPLAY, ownWindowIds: ['window:100:0'] })
  assert.equal(chosenId(list, 'screen:1:0'), 'screen:1:0')
  assert.equal(chosenId(list, 'window:7:0'), 'window:7:0')
  for (const id of ['window:100:0', 'screen:2:0', 'window:7:0 ', '', null, undefined, 7, { id: 'screen:1:0' }]) {
    assert.equal(chosenId(list, id), null, String(id))
  }
})

test('only a request for sound with no picture is answered without the picker', () => {
  assert.equal(soundOnly({ videoRequested: false, audioRequested: true }), true)
  assert.equal(soundOnly({ videoRequested: true, audioRequested: true }), false)
  assert.equal(soundOnly({ videoRequested: true, audioRequested: false }), false)
  assert.equal(soundOnly({ videoRequested: false, audioRequested: false }), false)
})
