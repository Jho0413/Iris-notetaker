const { contextBridge, ipcRenderer } = require('electron')

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
  changeKey: () => ipcRenderer.invoke('iris-app:change-key'),
  removeKey: () => ipcRenderer.invoke('iris-app:remove-key'),
  savePdf: (title) => ipcRenderer.invoke('iris-app:save-pdf', String(title)),
  recordingChanged: (phase, startedAt, macSoundRefused, screen) =>
    ipcRenderer.send('iris-app:recording', {
      phase: String(phase),
      startedAt: Number(startedAt),
      macSoundRefused: Boolean(macSoundRefused),
      screen: Boolean(screen),
    }),
  onRecordCommand: (listener) => {
    const relay = (_event, command) => {
      if (RECORD_COMMANDS.includes(command)) listener(command)
    }
    ipcRenderer.on('iris-app:record', relay)
    ipcRenderer.send('iris-app:record-listening')
    return () => ipcRenderer.removeListener('iris-app:record', relay)
  },
})
