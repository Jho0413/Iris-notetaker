function pagePath(url) {
  try {
    return new URL(url).pathname.replace(/\/+$/, '') || '/'
  } catch {
    return null
  }
}

function unloadPrompt(url, { quitting = false } = {}) {
  if (pagePath(url) === '/live') {
    if (quitting) return null
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
