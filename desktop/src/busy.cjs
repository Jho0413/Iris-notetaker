function names(busy) {
  const listed = busy.slice(0, 3).map((s) => `“${s.title || 'Untitled session'}”`).join(', ')
  return `${listed}${busy.length > 3 ? ' and others' : ''}`
}

function busyDetail(busy) {
  return (
    `${names(busy)} ${busy.length === 1 ? 'is' : 'are'} ` +
    'still recording or making notes. What is recorded so far is kept: resume it from the library later.'
  )
}

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

function busyNow(sessions, { capturing }) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => s?.status === 'processing' || (capturing && s?.status === 'recording'))
}

function recordingIds(sessions) {
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => s?.status === 'recording' && typeof s.id === 'string').map((s) => s.id)
}

const LIST_TIMEOUT_MS = 10_000

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
