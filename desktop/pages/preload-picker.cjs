// Preload for the picker only (pages/picker.html): what to record when the Live page starts. The
// main process also checks that each call comes from that page in the picker window, and that the
// chosen id is one it offered (src/picker.cjs).
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('irisPicker', {
  /** What to offer, taken once as the picker opened: {granted: false}, or {granted: true,
   * screens, windows, selected} with thumbnails and icons as data: URLs. */
  snapshot: () => ipcRenderer.invoke('iris-picker:snapshot'),
  /** Record the source with this id; the picker closes. */
  choose: (id) => ipcRenderer.invoke('iris-picker:choose', String(id)),
  /** Close the picker without recording. */
  cancel: () => ipcRenderer.invoke('iris-picker:cancel'),
  /** Open System Settings at Screen & System Audio Recording. */
  openSettings: () => ipcRenderer.invoke('iris-picker:open-settings'),
})
