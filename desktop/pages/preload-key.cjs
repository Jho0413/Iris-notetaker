// Preload for the key screen only (pages/key.html). The main process also checks that each call
// comes from that page in the key window.
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('irisKey', {
  /** Store the key (encrypted). Resolves {ok: true}, or {ok: false, error} to show. */
  save: (key) => ipcRenderer.invoke('iris-key:save', String(key ?? '')),
  /** Whether a key is stored already. */
  hasApiKey: () => ipcRenderer.invoke('iris-key:has'),
  /** Close the screen without changing the key. */
  cancel: () => ipcRenderer.invoke('iris-key:cancel'),
})
