// The key screen. window.irisKey comes from preload-key.cjs: save() and cancel(); the stored
// key is never sent back to this page. Saving checks the new key with
// OpenAI first.
//
// mode=first (first run): two steps, Welcome then Connect, with Next and Back; "Set up later"
// opens Iris without a key. mode=add / mode=change (from the app): the Connect form on its own,
// and the app restarts its engine with the new key.
const welcome = document.getElementById('welcome')
const connect = document.getElementById('connect')
const form = document.getElementById('form')
const input = document.getElementById('key')
const error = document.getElementById('error')
const save = document.getElementById('save')
const cancel = document.getElementById('cancel')
const back = document.getElementById('back')

const mode = new URLSearchParams(location.search).get('mode')
if (mode === 'change') document.getElementById('title').textContent = 'Change your OpenAI key'
if (mode === 'change' || mode === 'add') save.textContent = 'Save'
if (mode === 'first') {
  cancel.textContent = 'Set up later'
  back.hidden = false
}
const saveLabel = save.textContent

/** Show one step; the other is hidden, so the window only ever holds one. */
function show(step) {
  welcome.hidden = step !== 'welcome'
  connect.hidden = step !== 'connect'
  if (step === 'welcome') document.getElementById('next').focus()
  else input.focus()
}

document.getElementById('next').addEventListener('click', () => show('connect'))
back.addEventListener('click', () => show('welcome'))
cancel.addEventListener('click', () => window.irisKey.cancel())
show(mode === 'first' ? 'welcome' : 'connect')

form.addEventListener('submit', async (e) => {
  e.preventDefault()
  error.textContent = ''
  for (const button of [save, cancel, back]) button.disabled = true
  save.textContent = 'Checking with OpenAI…'
  try {
    const result = await window.irisKey.save(input.value)
    if (!result.ok) {
      error.textContent = result.error
      input.focus()
    }
  } catch {
    error.textContent = 'Iris could not save the key. Try again.'
  } finally {
    for (const button of [save, cancel, back]) button.disabled = false
    save.textContent = saveLabel
  }
})
