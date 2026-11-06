// The picker: what to record when a recording starts. window.irisPicker comes from
// preload-picker.cjs. The list is a snapshot taken as the picker opened (src/picker.cjs decides
// what is in it and what is selected); this page only shows it and sends back the choice.
//
// Click selects, double-click or Return records the selection, the arrow keys move it, Esc
// cancels. Window titles come from other apps: they are set as text, never as HTML.
const sources = document.getElementById('sources')
const record = document.getElementById('record')
const template = document.getElementById('card')
let selected = null

function cards() {
  return [...sources.querySelectorAll('.card')]
}

function select(id, { scroll = true } = {}) {
  selected = id
  for (const card of cards()) {
    const on = card.dataset.id === id
    card.setAttribute('aria-checked', String(on))
    card.tabIndex = on ? 0 : -1 // one tab stop for the group; the arrows move within it
    if (on) card.focus({ preventScroll: !scroll })
  }
  record.disabled = selected === null
}

function choose() {
  if (selected !== null) window.irisPicker.choose(selected)
}

function card(item, thumbnail, label) {
  const node = template.content.firstElementChild.cloneNode(true)
  node.dataset.id = item.id
  if (thumbnail) {
    const img = document.createElement('img')
    img.src = thumbnail
    img.alt = ''
    node.querySelector('.thumb').append(img)
  }
  node.querySelector('.label').append(...label)
  node.addEventListener('click', () => select(item.id))
  node.addEventListener('dblclick', () => {
    select(item.id)
    choose()
  })
  return node
}

function text(tag, value, className) {
  const node = document.createElement(tag)
  node.textContent = value
  if (className) node.className = className
  return node
}

function screenCard(screen) {
  const label = [text('strong', screen.label)]
  if (screen.detail) label.push(text('span', screen.detail))
  if (screen.recommended) label.push(text('span', 'Recommended', 'badge'))
  const node = card(screen, screen.thumbnail, label)
  node.setAttribute('aria-label', [screen.label, screen.detail].filter(Boolean).join(', '))
  return node
}

function windowCard(win) {
  const label = []
  if (win.icon) {
    const icon = document.createElement('img')
    icon.className = 'appicon'
    icon.src = win.icon
    icon.alt = ''
    label.push(icon)
  }
  const title = text('span', win.title, 'title')
  title.title = win.title
  label.push(title)
  const node = card(win, win.thumbnail, label)
  node.setAttribute('aria-label', win.title)
  return node
}

function show(snapshot) {
  if (!snapshot.granted) {
    document.getElementById('no-permission').hidden = false
    document.getElementById('settings').focus()
    return
  }
  const screens = document.getElementById('screens')
  screens.classList.toggle('one', snapshot.screens.length === 1)
  screens.append(...snapshot.screens.map(screenCard))
  document.getElementById('windows').append(...snapshot.windows.map(windowCard))
  document.getElementById('no-windows').hidden = snapshot.windows.length > 0
  sources.hidden = false
  document.getElementById('audio').hidden = false
  record.hidden = false
  // Opening at the top: the thumbnails are still loading, so scrolling to the card now would
  // overshoot and hide the "Entire screen" heading.
  select(snapshot.selected, { scroll: false })
  if (selected === null) record.focus()
}

record.addEventListener('click', choose)
document.getElementById('cancel').addEventListener('click', () => window.irisPicker.cancel())
document.getElementById('settings').addEventListener('click', () => window.irisPicker.openSettings())

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    e.preventDefault()
    window.irisPicker.cancel()
    return
  }
  // A focused footer button (Cancel, Open System Settings) answers Return itself.
  if (e.key === 'Enter' && !record.hidden && !e.target.closest('footer button:not(#record), #settings')) {
    e.preventDefault()
    choose()
    return
  }
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key]
  const all = cards()
  const at = all.findIndex((c) => c.dataset.id === selected)
  if (!step || at < 0) return
  e.preventDefault()
  select(all[Math.max(0, Math.min(all.length - 1, at + step))].dataset.id)
})

window.irisPicker.snapshot().then(show, () => window.irisPicker.cancel())
