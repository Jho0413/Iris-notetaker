const TRAY_PHASE = {
  idle: 'idle',
  requesting: 'starting',
  recording: 'recording',
  stopped: 'stopped',
  continuing: 'continuing',
  stopping: 'stopping',
  finishing: 'stopping',
  unfinished: 'unfinished',
}

const IDLE = Object.freeze({ phase: 'idle', startedAt: 0, macSoundRefused: false, screen: false })

function parseReport(report) {
  const name = report?.phase
  if (typeof name !== 'string' || !Object.hasOwn(TRAY_PHASE, name)) return null
  const phase = TRAY_PHASE[name]
  const macSoundRefused = report.macSoundRefused === true
  if (phase !== 'recording') return { phase, startedAt: 0, macSoundRefused, screen: false }
  const { startedAt } = report
  if (typeof startedAt !== 'number' || !Number.isFinite(startedAt) || startedAt <= 0) return null
  if (typeof report.macSoundRefused !== 'boolean' || typeof report.screen !== 'boolean') return null
  return { phase, startedAt, macSoundRefused, screen: report.screen }
}

function clock(ms) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

const SHOW = { id: 'show', label: 'Show Iris' }
const SHARE_SCREEN = { id: 'shareScreen', label: 'Share Screen…' }
const STOP_SHARING = { id: 'stopSharing', label: 'Stop Sharing' }
const QUIT = { id: 'quit', label: 'Quit Iris' }
const SEPARATOR = { type: 'separator' }
const status = (label) => ({ label, enabled: false })

function trayView(state, now) {
  switch (state.phase) {
    case 'recording':
      return {
        recording: true,
        title: clock(now - state.startedAt),
        items: [
          status('Recording'),
          state.screen ? STOP_SHARING : SHARE_SCREEN,
          { id: 'stop', label: 'Stop' },
          SHOW,
          SEPARATOR,
          QUIT,
        ],
      }
    case 'stopped':
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

function hideOnClose(state) {
  return ['recording', 'stopped', 'continuing', 'stopping'].includes(state.phase)
}

const asking = (phase) => phase === 'starting' || phase === 'continuing'

function holdsRecording(state) {
  return state.phase !== 'idle' && state.phase !== 'starting'
}

const STOPPED_CHOICES = {
  continue: { send: 'continue', show: false, quiet: true },
  makeNotes: { send: 'make-notes', show: false },
  startOver: { send: 'start-over', show: true },
  discard: { send: 'discard', show: true },
}

const RELAY_START = Object.freeze({ recording: IDLE, listening: false, startPending: null, quietStart: false })

const START_COMMAND = { audio: 'start-audio', screen: 'start-screen' }

const START_ANSWER_MS = 3000

function relay(state, event) {
  switch (event.type) {
    case 'record': {
      const command = START_COMMAND[event.mode]
      if (!command || state.recording.phase !== 'idle') return { state }
      if (state.listening) return { state: { ...state, startPending: null, quietStart: true }, send: command }
      return { state: { ...state, startPending: command, quietStart: true } }
    }
    case 'screen': {
      const { recording } = state
      if (!state.listening || recording.phase !== 'recording' || recording.screen === event.on) return { state }
      return { state, send: event.on ? 'share-screen' : 'stop-sharing' }
    }
    case 'stop':
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
      const stopFailed = state.recording.phase === 'stopping' && recording.phase === 'unfinished'
      const startEnded = asking(state.recording.phase) && !asking(recording.phase)
      const backTo = recording.phase === 'idle' || recording.phase === 'stopped'
      const startFailed = state.quietStart && startEnded && backTo
      const soundRefused = recording.macSoundRefused && !state.recording.macSoundRefused
      const quietStart = state.quietStart && !startEnded
      const show = stopFailed || startFailed || soundRefused
      const closePicker = state.recording.phase === 'recording' && recording.phase !== 'recording'
      return {
        state: { ...state, recording, quietStart },
        ...(show && { show: true }),
        ...(closePicker && { closePicker: true }),
      }
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
