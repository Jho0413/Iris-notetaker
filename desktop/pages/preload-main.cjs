// Preload for the main window: the Iris UI's only way to ask the app for something. Three actions,
// the recording state for the menu bar icon, and its start/stop commands; nothing to read: the key
// itself never reaches the page. The main process also checks that each call comes from the Iris
// page's top frame in the main window (policy.fromIrisWindow).
const { contextBridge, ipcRenderer } = require('electron')

/** What the menu bar sends the page: start a recording, turn the screen on or off, Stop, or what
 * to do once stopped. A sandboxed preload can't require app files, so the list is here (tray.cjs's
 * relay sends them). */
const RECORD_COMMANDS = [
  'start-audio',
  'start-screen',
  'share-screen',
  'stop-sharing',
  'stop',
  'continue',
  'make-notes',
  'start-over',
  'discard',
]

contextBridge.exposeInMainWorld('irisApp', {
  /** Open the key window to add or replace the key; the engine restarts with a new one.
   * Resolves {done: true} once it is saved and the engine is back, {done: false} if cancelled. */
  changeKey: () => ipcRenderer.invoke('iris-app:change-key'),
  /** Ask (a dialog from the app) and remove the stored key; the engine restarts without it.
   * Resolves {done: true} once removed and the engine is back, {done: false} if cancelled. */
  removeKey: () => ipcRenderer.invoke('iris-app:remove-key'),
  /** Save the page, as printed, as a PDF named after `title`, where the user chooses.
   * Resolves {done: true} once saved, {done: false} if cancelled. */
  savePdf: (title) => ipcRenderer.invoke('iris-app:save-pdf', String(title)),
  /** The Live page's phase, when recording started (ms since the epoch), whether macOS refused
   * the Mac's sound to the recording, and whether the screen is shared now, for the menu bar icon
   * (desktop/src/tray.cjs). */
  recordingChanged: (phase, startedAt, macSoundRefused, screen) =>
    ipcRenderer.send('iris-app:recording', {
      phase: String(phase),
      startedAt: Number(startedAt),
      macSoundRefused: Boolean(macSoundRefused),
      screen: Boolean(screen),
    }),
  /** Call `listener` with the menu bar's command (RECORD_COMMANDS) when it asks. Returns the
   * unsubscribe. */
  onRecordCommand: (listener) => {
    const relay = (_event, command) => {
      if (RECORD_COMMANDS.includes(command)) listener(command)
    }
    ipcRenderer.on('iris-app:record', relay)
    ipcRenderer.send('iris-app:record-listening')
    return () => ipcRenderer.removeListener('iris-app:record', relay)
  },
})
