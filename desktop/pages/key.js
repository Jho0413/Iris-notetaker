// The key screen. window.irisKey comes from preload-key.cjs: save() and cancel() only; the
// stored key is never sent back to this page.
const form = document.getElementById('form')
const input = document.getElementById('key')
const error = document.getElementById('error')
const save = document.getElementById('save')
const cancel = document.getElementById('cancel')

if (new URLSearchParams(location.search).get('mode') === 'change') {
  document.getElementById('title').textContent = 'Change the OpenAI API key'
  save.textContent = 'Save and restart'
  cancel.hidden = false
}
cancel.addEventListener('click', () => window.irisKey.cancel())
input.focus()

form.addEventListener('submit', async (e) => {
  e.preventDefault()
  error.textContent = ''
  save.disabled = true
  try {
    const result = await window.irisKey.save(input.value)
    if (!result.ok) {
      error.textContent = result.error
      input.focus()
    }
  } catch {
    error.textContent = 'Iris could not save the key. Try again.'
  } finally {
    save.disabled = false
  }
})
