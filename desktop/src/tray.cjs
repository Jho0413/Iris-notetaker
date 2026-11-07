// The menu bar icon (Electron Tray), as pure functions (tested in test/tray.test.cjs). Recording
// itself happens in the Live page: it reports its phase over the bridge (pages/preload-main.cjs),
// relay() decides when the page is sent "start" or "stop", and main.cjs shows what trayView() says.

/** The Live page's phases (frontend/src/pages/LivePage.tsx), as the tray groups them. */
const TRAY_PHASE = {
  idle: 'idle',
  requesting: 'starting',
  recording: 'recording',
  stopped: 'stopped',
  continuing: 'continuing', // asking for the sources again; like a start, but the recording is open
  stopping: 'stopping',
  finishing: 'stopping',
  unfinished: 'unfinished',
}

const IDLE = Object.freeze({ phase: 'idle', startedAt: 0, macSoundRefused: false })

/**
 * A report from the page ({phase, startedAt, macSoundRefused}), checked once here: the tray's
 * state, or null for anything malformed. `startedAt` (ms since the epoch) matters only while
 * recording: a page that goes away still uploading reports "stopping" without one.
 * `macSoundRefused`: macOS refused the Mac's sound to this audio recording, so only the mic is
 * recorded; required while recording, and kept while stopped and continuing, so the window shows
 * once per recording, not again after each Continue.
 */
function parseReport(report) {
  const name = report?.phase
  if (typeof name !== 'string' || !Object.hasOwn(TRAY_PHASE, name)) return null
  const phase = TRAY_PHASE[name]
  const macSoundRefused = report.macSoundRefused === true
  if (phase !== 'recording') return { phase, startedAt: 0, macSoundRefused }
  const { startedAt } = report
  if (typeof startedAt !== 'number' || !Number.isFinite(startedAt) || startedAt <= 0) return null
  if (typeof report.macSoundRefused !== 'boolean') return null
  return { phase, startedAt, macSoundRefused }
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
 * main.cjs runs: 'recordAudio', 'recordScreen', 'stop', a stopped recording's 'continue',
 * 'makeNotes', 'startOver' or 'discard', 'show' or 'quit'.
 */
function trayView(state, now) {
  switch (state.phase) {
    case 'recording':
      return {
        recording: true,
        title: clock(now - state.startedAt),
        items: [status('Recording'), { id: 'stop', label: 'Stop' }, SHOW, SEPARATOR, QUIT],
      }
    case 'stopped':
      // Start Over and Discard delete the recording: the window shows to confirm them.
      return {
        recording: false,
        title: '',
        items: [
          status('Stopped'),
          { id: 'continue', label: 'Continue Recording' },
          { id: 'makeNotes', label: 'Make Notes' },
          { id: 'startOver', label: 'Start Over…' },
          { id: 'discard', label: 'Discard Recording…' },
          SHOW,
          SEPARATOR,
          QUIT,
        ],
      }
    case 'stopping':
      return { recording: true, title: '', items: [status('Finishing the upload…'), SHOW, SEPARATOR, QUIT] }
    case 'starting':
      return { recording: false, title: '', items: [status('Starting…'), SHOW, SEPARATOR, QUIT] }
    case 'continuing':
      return { recording: false, title: '', items: [status('Continuing…'), SHOW, SEPARATOR, QUIT] }
    case 'unfinished':
      // The Live page offers Try again: the recording is kept, its notes not started yet.
      return { recording: false, title: '', items: [status('Recording stopped'), SHOW, SEPARATOR, QUIT] }
    default:
      return {
        recording: false,
        title: '',
        items: [
          { id: 'recordAudio', label: 'Record Audio' },
          { id: 'recordScreen', label: 'Record Screen…' },
          { id: 'show', label: 'Open Iris' },
          SEPARATOR,
          QUIT,
        ],
      }
  }
}

/**
 * Whether closing the window hides it instead: while capture runs, closing would end the
 * recording, and the menu bar can still stop it and show the window again.
 */
function hideOnClose(state) {
  return ['recording', 'stopped', 'continuing', 'stopping'].includes(state.phase)
}

/** Asking for sources (a start, or a stopped recording's Continue), so a picker may be open. */
const asking = (phase) => phase === 'starting' || phase === 'continuing'

/**
 * Whether the Live page holds a recording: capturing, stopped, continuing, or still uploading
 * one. A start that is only asking for permissions holds none (its session is made once they are
 * granted), so quitting then cuts nothing short.
 */
function holdsRecording(state) {
  return state.phase !== 'idle' && state.phase !== 'starting'
}

/** A stopped recording's choices from the menu bar: the command each sends the page, and whether
 * the window shows (the page confirms the ones that delete the recording). */
const STOPPED_CHOICES = {
  // Like a start from the menu bar: quiet (only a picker shows), and the window shows if it fails.
  continue: { send: 'continue', show: false, quiet: true },
  makeNotes: { send: 'make-notes', show: false },
  startOver: { send: 'start-over', show: true },
  discard: { send: 'discard', show: true },
}

/**
 * The relay between the menu bar and the main window's UI. `recording`: what the Live page last
 * reported. `listening`: the UI has subscribed to commands. `startPending`: the start command
 * ('start-audio' or 'start-screen') waiting for the UI to load (the window was closed, or the
 * engine is still starting), or null. `quietStart`: a start (or a stopped recording's Continue)
 * from the menu bar is under way with the window left as it was (hidden, or in another Space):
 * only the picker shows (none for audio), and the window only if it fails.
 */
const RELAY_START = Object.freeze({ recording: IDLE, listening: false, startPending: null, quietStart: false })

/** The command each menu bar start sends the page. */
const START_COMMAND = { audio: 'start-audio', screen: 'start-screen' }

/** How long a page sent "start" has to report it is starting. It does within a render; one that
 * doesn't is held up by something it shows (e.g. "Leave without saving?" on another page). */
const START_ANSWER_MS = 3000

/**
 * The relay's next state for `event`, and what main.cjs does about it: `send` a command to the UI
 * ('start-audio', 'start-screen', 'stop', or a STOPPED_CHOICES command), `show` the window, or log
 * a `malformed` report. Events:
 *   record        "Record Audio" or "Record Screen…", `event.mode` 'audio' or 'screen' (main.cjs
 *                 makes the window if it was closed, but doesn't show it)
 *   stop          "Stop": the page stops capturing and offers what next
 *   choice        a stopped recording's choice, `event.choice` a STOPPED_CHOICES key
 *   listening     the UI subscribed (after each load of the UI)
 *   report        the Live page's phase, `event.report` as sent over the bridge
 *   pageGone      the page was replaced (a reload, the UI after the starting screen) or crashed
 *   windowClosed  the main window is gone
 *   pickerCancelled  the picker was cancelled: the user's choice, so a failed start shows nothing
 *   startUnanswered  START_ANSWER_MS after a quiet start was sent: a page still idle is held up
 *                 by something it asks, in a window the user may not see
 *   engineFailed  the engine didn't start: a start waiting for the UI is dropped, so a later
 *                 Restart doesn't start recording without being asked again
 */
function relay(state, event) {
  switch (event.type) {
    case 'record': {
      const command = START_COMMAND[event.mode]
      if (!command || state.recording.phase !== 'idle') return { state }
      if (state.listening) return { state: { ...state, startPending: null, quietStart: true }, send: command }
      return { state: { ...state, startPending: command, quietStart: true } }
    }
    case 'stop':
      // Never queued: a page that loads later has nothing to stop.
      return state.listening && state.recording.phase === 'recording' ? { state, send: 'stop' } : { state }
    case 'choice': {
      const choice = Object.hasOwn(STOPPED_CHOICES, event.choice) ? STOPPED_CHOICES[event.choice] : null
      if (!choice || !state.listening || state.recording.phase !== 'stopped') return { state }
      const next = choice.quiet ? { ...state, quietStart: true } : state
      return { state: next, send: choice.send, ...(choice.show && { show: true }) }
    }
    case 'listening':
      if (state.startPending) {
        return { state: { ...state, listening: true, startPending: null }, send: state.startPending }
      }
      return { state: { ...state, listening: true } }
    case 'report': {
      const recording = parseReport(event.report)
      if (!recording) return { state, malformed: true }
      // A stop that failed (the Live page's Try again): the window may be hidden, so show it.
      const stopFailed = state.recording.phase === 'stopping' && recording.phase === 'unfinished'
      // A quiet start (or Continue) that ended without recording, and wasn't cancelled in the
      // picker: the page says why.
      const startEnded = asking(state.recording.phase) && !asking(recording.phase)
      const backTo = recording.phase === 'idle' || recording.phase === 'stopped'
      const startFailed = state.quietStart && startEnded && backTo
      // macOS refused the Mac's sound (at the start or later): the page says how to allow it,
      // and a call's other side isn't recorded meanwhile, so show it even mid-call, once.
      const soundRefused = recording.macSoundRefused && !state.recording.macSoundRefused
      const quietStart = state.quietStart && !startEnded
      const show = stopFailed || startFailed || soundRefused
      return { state: { ...state, recording, quietStart }, ...(show && { show: true }) }
    }
    case 'pickerCancelled':
      return { state: { ...state, quietStart: false } }
    case 'startUnanswered':
      if (!state.quietStart || state.recording.phase !== 'idle') return { state }
      return { state: { ...state, quietStart: false }, show: true }
    case 'pageGone':
      return { state: { ...state, recording: IDLE, listening: false, quietStart: false } }
    case 'windowClosed':
      return { state: RELAY_START }
    case 'engineFailed': {
      // The engine's dialog is about to ask: a quiet start shows the window it belongs to.
      const show = state.startPending && state.quietStart
      return { state: { ...state, startPending: null, quietStart: false }, ...(show && { show: true }) }
    }
    default:
      return { state }
  }
}

module.exports = {
  IDLE,
  RELAY_START,
  START_ANSWER_MS,
  parseReport,
  trayView,
  clock,
  hideOnClose,
  holdsRecording,
  relay,
}
