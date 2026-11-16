const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('irisKey', {
  save: (key) => ipcRenderer.invoke('iris-key:save', String(key ?? '')),
  cancel: () => ipcRenderer.invoke('iris-key:cancel'),
})
