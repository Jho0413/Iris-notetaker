// The menu bar icon (Electron Tray), as pure functions (tested in test/tray.test.cjs). Recording
// itself happens in the Live page: it reports its phase over the bridge (pages/preload-main.cjs),
// and main.cjs shows what trayView() says and sends the page "start" or "stop".

/** The Live page's phases (frontend/src/pages/LivePage.tsx), as the tray groups them. */
const TRAY_PHASE = {
  idle: 'idle',
  requesting: 'starting',
  recording: 'recording',
  stopping: 'stopping',
  finishing: 'stopping',
  unfinished: 'unfinished',
}

const IDLE = Object.freeze({ phase: 'idle', startedAt: 0 })

/**
 * A report from the page ({phase, startedAt}), checked once here: the tray's state, or null for
 * anything malformed. `startedAt` (ms since the epoch) matters only while recording.
 */
function parseReport(report) {
  const name = report?.phase
  if (typeof name !== 'string' || !Object.hasOwn(TRAY_PHASE, name)) return null
  const phase = TRAY_PHASE[name]
  if (phase !== 'recording' && phase !== 'stopping') return { phase, startedAt: 0 }
  const startedAt = report.startedAt
  if (typeof startedAt !== 'number' || !Number.isFinite(startedAt) || startedAt <= 0) return null
  return { phase, startedAt }
}

/** Elapsed time as the Live page's clock shows it: "4:05", "1:02:03". */
function clock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

const SHOW = { id: 'show', label: 'Show Iris' }
const QUIT = { id: 'quit', label: 'Quit Iris' }
const SEPARATOR = { type: 'separator' }
const status = (label) => ({ label, enabled: false })

/**
 * What the icon shows in `state` at `now`: `recording` (the red icon), `title` (the text beside
 * it: the elapsed time while recording) and the menu `items`. An item with an `id` is an action
 * main.cjs runs: 'record', 'stop', 'show' or 'quit'.
 */
function trayView(state, now) {
  switch (state.phase) {
    case 'recording':
      return {
        recording: true,
        title: clock(now - state.startedAt),
        items: [status('Recording'), { id: 'stop', label: 'Stop and Make Notes' }, SHOW, SEPARATOR, QUIT],
      }
    case 'stopping':
      return { recording: true, title: '', items: [status('Finishing the upload…'), SHOW, SEPARATOR, QUIT] }
    case 'starting':
      return { recording: false, title: '', items: [status('Starting…'), SHOW, SEPARATOR, QUIT] }
    case 'unfinished':
      // The Live page offers Try again: the recording is kept, its notes not started yet.
      return { recording: false, title: '', items: [status('Recording stopped'), SHOW, SEPARATOR, QUIT] }
    default:
      return {
        recording: false,
        title: '',
        items: [{ id: 'record', label: 'Record Screen…' }, { id: 'show', label: 'Open Iris' }, SEPARATOR, QUIT],
      }
  }
}

/**
 * Whether closing the window hides it instead: while capture runs, closing would end the
 * recording, and the menu bar can still stop it and show the window again.
 */
function hideOnClose(state) {
  return state.phase === 'recording' || state.phase === 'stopping'
}

module.exports = { IDLE, parseReport, trayView, clock, hideOnClose }
