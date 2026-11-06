// The menu bar icon (Electron Tray), as pure functions (tested in test/tray.test.cjs). Recording
// itself happens in the Live page: it reports its phase over the bridge (pages/preload-main.cjs),
// relay() decides when the page is sent "start" or "stop", and main.cjs shows what trayView() says.

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
 * anything malformed. `startedAt` (ms since the epoch) matters only while recording: a page that
 * goes away still uploading reports "stopping" without one.
 */
function parseReport(report) {
  const name = report?.phase
  if (typeof name !== 'string' || !Object.hasOwn(TRAY_PHASE, name)) return null
  const phase = TRAY_PHASE[name]
  if (phase !== 'recording') return { phase, startedAt: 0 }
  const startedAt = report.startedAt
  if (typeof startedAt !== 'number' || !Number.isFinite(startedAt) || startedAt <= 0) return null
  return { phase, startedAt }
}

/** Elapsed time as the Live page's clock shows it (formatClock in frontend/src/session/state.ts):
 * "4:05", "1:02:03". */
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

/**
 * The relay between the menu bar and the main window's UI. `recording`: what the Live page last
 * reported. `listening`: the UI has subscribed to commands. `startPending`: "Record Screen…" is
 * waiting for the UI to load (the window was closed, or the engine is still starting).
 */
const RELAY_START = Object.freeze({ recording: IDLE, listening: false, startPending: false })

/**
 * The relay's next state for `event`, and what main.cjs does about it: `send` a command to the UI
 * ('start' or 'stop'), `show` the window, or log a `malformed` report. Events:
 *   record        "Record Screen…" (main.cjs shows the window first)
 *   stop          "Stop and Make Notes"
 *   listening     the UI subscribed (after each load of the UI)
 *   report        the Live page's phase, `event.report` as sent over the bridge
 *   pageGone      the page was replaced (a reload, the UI after the starting screen) or crashed
 *   windowClosed  the main window is gone
 *   engineFailed  the engine didn't start: a start waiting for the UI is dropped, so a later
 *                 Restart doesn't start recording without being asked again
 */
function relay(state, event) {
  switch (event.type) {
    case 'record':
      if (state.recording.phase !== 'idle') return { state }
      if (state.listening) return { state: { ...state, startPending: false }, send: 'start' }
      return { state: { ...state, startPending: true } }
    case 'stop':
      // Never queued: a page that loads later has nothing to stop.
      return state.listening && state.recording.phase === 'recording' ? { state, send: 'stop' } : { state }
    case 'listening':
      if (state.startPending) return { state: { ...state, listening: true, startPending: false }, send: 'start' }
      return { state: { ...state, listening: true } }
    case 'report': {
      const recording = parseReport(event.report)
      if (!recording) return { state, malformed: true }
      // A stop that failed (the Live page's Try again): the window may be hidden, so show it.
      const show = state.recording.phase === 'stopping' && recording.phase === 'unfinished'
      return { state: { ...state, recording }, ...(show && { show: true }) }
    }
    case 'pageGone':
      return { state: { ...state, recording: IDLE, listening: false } }
    case 'windowClosed':
      return { state: { recording: IDLE, listening: false, startPending: false } }
    case 'engineFailed':
      return { state: { ...state, startPending: false } }
    default:
      return { state }
  }
}

module.exports = { IDLE, RELAY_START, parseReport, trayView, clock, hideOnClose, relay }
