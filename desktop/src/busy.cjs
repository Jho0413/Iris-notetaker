// Sessions still recording or making notes, and the words for dialogs about them (main.cjs).
// Sessions are those from /api/sessions with status "recording" or "processing".

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
 * "Quit Iris?": what quitting does to each busy session. A recording is stopped (the app stops it
 * before the engine) and the library offers Make notes; notes being made are cut off, and Try
 * again resumes them.
 */
function quitDetail(busy) {
  const recording = busy.filter((s) => s?.status === 'recording')
  const making = busy.filter((s) => s?.status !== 'recording')
  const lines = []
  if (recording.length) {
    const one = recording.length === 1
    lines.push(
      `${names(recording)} ${one ? 'is' : 'are'} still recording. Quitting stops the ` +
        `${one ? 'recording' : 'recordings'} and keeps what was recorded: make notes from ` +
        `${one ? 'it' : 'them'} in the library.`,
    )
  }
  if (making.length) {
    const one = making.length === 1
    lines.push(
      `${names(making)} ${one ? 'is' : 'are'} still making notes. Quitting stops ` +
        `${one ? 'it' : 'them'} and keeps the finished steps: Try again in the library carries on.`,
    )
  }
  return lines.join('\n\n')
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

/**
 * The sessions that quitting, or restarting the engine, would cut short: from /api/sessions, those
 * making notes, and those recording only while `capturing` (the Live page has a capture going, or
 * holds what it captured, still to upload). Any other "recording" session isn't busy: nothing in
 * this app is capturing it (the app stops those it finds, `stopRecordings`).
 */
function busyNow(sessions, { capturing }) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => s?.status === 'processing' || (capturing && s?.status === 'recording'))
}

/**
 * The sessions still "recording", to stop without notes when no page of the app can be recording
 * them: at launch (left by a crash), at quit, and when the window's page crashes. The app owns its
 * engine and the only page that records into it, so none is still being captured then.
 */
function recordingIds(sessions) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => s?.status === 'recording' && typeof s.id === 'string').map((s) => s.id)
}

// The session list can take a while: the engine adds up each session's cost from its call log.
const LIST_TIMEOUT_MS = 10_000

/**
 * Stop every session still "recording" without notes (POST /live/{id}/stop; the library then
 * offers Make notes or Delete) and wait for the engine's answers. `backend` is the app's Backend
 * (getJson, post). The list is read first, so a recording started after that is never stopped
 * here. A list that can't be read stops nothing and is logged: such a leftover stays "recording"
 * until the app next stops recordings (at quit or the next launch). Never rejects.
 */
async function stopRecordings(backend, why, log) {
  const sessions = await backend.getJson('/api/sessions', LIST_TIMEOUT_MS)
  if (!Array.isArray(sessions)) {
    log(`[sessions] couldn't read the sessions to stop recordings (${why}): none stopped`)
    return
  }
  const ids = recordingIds(sessions)
  if (!ids.length) return
  const answers = await Promise.all(ids.map((id) => backend.post(`/api/live/${encodeURIComponent(id)}/stop`)))
  log(`[sessions] stopped recordings (${why}): ${ids.map((id, i) => `${id} ${answers[i] || 'no answer'}`).join(', ')}`)
}

module.exports = { busyDetail, busyKeyRefusal, busyNow, quitDetail, recordingIds, stopRecordings }
