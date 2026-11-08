// What to ask when an Iris page refuses to unload (its beforeunload handler), as a pure function
// (tested in test/unload.test.cjs). main.cjs shows the answer from will-prevent-unload, since
// Electron would otherwise cancel the close or reload without a word.
//
// Pages refuse for one of two reasons: the live page while it records; the Settings page, or a
// session page whose notes are being edited, while a change is unsaved. The page is told apart by
// its path, so no bridge to the page is needed.

/** The path of an app://iris URL ("/live"), or null if it can't be parsed. */
function pagePath(url) {
  try {
    return new URL(url).pathname.replace(/\/+$/, '') || '/'
  } catch {
    return null
  }
}

/**
 * The dialog for a page at `url` that refuses to unload, or null to let it unload without asking.
 * `quitting`: the user already confirmed quitting Iris, which asked about a recording.
 * The answer's `leave` is the index of the button that lets the page go.
 */
function unloadPrompt(url, { quitting = false } = {}) {
  if (pagePath(url) === '/live') {
    if (quitting) return null // "Quit Iris?" already asked: a recording counts as busy
    return {
      type: 'warning',
      buttons: ['Stop Recording', 'Keep Recording'],
      defaultId: 1,
      cancelId: 1,
      leave: 0,
      message: 'Stop recording?',
      detail: 'Iris is recording. If you leave this page, the recording stops and what was recorded is kept: make notes from it in the library.',
    }
  }
  return {
    type: 'warning',
    buttons: [quitting ? 'Quit' : 'Leave', 'Keep Editing'],
    defaultId: 1,
    cancelId: 1,
    leave: 0,
    message: quitting ? 'Quit without saving?' : 'Leave without saving?',
    detail: 'Your changes on this page haven’t been saved.',
  }
}

module.exports = { unloadPrompt }
