const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('irisPicker', {
  snapshot: () => ipcRenderer.invoke('iris-picker:snapshot'),
  choose: (id) => ipcRenderer.invoke('iris-picker:choose', String(id)),
  cancel: () => ipcRenderer.invoke('iris-picker:cancel'),
  openSettings: () => ipcRenderer.invoke('iris-picker:open-settings'),
})
