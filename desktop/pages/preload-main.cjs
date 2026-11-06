// Preload for the main window: the Iris UI's only way to ask the app for something. Three actions,
// the recording state for the menu bar icon, and its start/stop commands; nothing to read: the key
// itself never reaches the page. The main process also checks that each call comes from the Iris
// page's top frame in the main window (policy.fromIrisWindow).
const { contextBridge, ipcRenderer } = require('electron')

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
  /** The Live page's phase, and when recording started (ms since the epoch), for the menu bar
   * icon (desktop/src/tray.cjs). */
  recordingChanged: (phase, startedAt) =>
    ipcRenderer.send('iris-app:recording', { phase: String(phase), startedAt: Number(startedAt) }),
  /** Call `listener` with "start" or "stop" when the menu bar asks. Returns the unsubscribe. */
  onRecordCommand: (listener) => {
    const relay = (_event, command) => {
      if (command === 'start' || command === 'stop') listener(command)
    }
    ipcRenderer.on('iris-app:record', relay)
    ipcRenderer.send('iris-app:record-listening')
    return () => ipcRenderer.removeListener('iris-app:record', relay)
  },
})
