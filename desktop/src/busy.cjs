// The words for dialogs about sessions still recording or making notes (main.cjs). Sessions are
// those from /api/sessions with status "recording" or "processing".

function names(busy) {
  const listed = busy.slice(0, 3).map((s) => `“${s.title || 'Untitled session'}”`).join(', ')
  return `${listed}${busy.length > 3 ? ' and others' : ''}`
}

/** "“Lecture 3” is still recording or making notes…", for a dialog that would stop the engine. */
function busyDetail(busy) {
  return (
    `${names(busy)} ${busy.length === 1 ? 'is' : 'are'} ` +
    'still recording or making notes. What is recorded so far is kept: resume it from the library later.'
  )
}

/**
 * The dialog refusing to add, change or remove the key while a session is busy (`action`: 'add',
 * 'change' or 'remove'). Each restarts the engine, which would stop a recording or a run half
 * done; Restart Engine stays available for a stuck engine.
 */
function busyKeyRefusal(busy, action) {
  const one = busy.length === 1
  const verb = { add: 'Add', change: 'Change', remove: 'Remove' }[action]
  return {
    type: 'info',
    buttons: ['OK'],
    defaultId: 0,
    message: 'Iris is still busy.',
    detail:
      `${names(busy)} ${one ? 'is' : 'are'} still recording or making notes. ` +
      `${verb} the key once ${one ? 'it has' : 'they have'} finished.`,
  }
}

module.exports = { busyDetail, busyKeyRefusal }
