// Preload for the main window: the Iris UI's only way to ask the app for something. Two actions,
// nothing to read: the key itself never reaches the page. The main process also checks that each
// call comes from the Iris page's top frame in the main window (policy.fromIrisWindow).
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('irisApp', {
  /** Open the key window to add or replace the key; the engine restarts with a new one.
   * Resolves {done: true} once it is saved and the engine is back, {done: false} if cancelled. */
  changeKey: () => ipcRenderer.invoke('iris-app:change-key'),
  /** Ask (a dialog from the app) and remove the stored key; the engine restarts without it.
   * Resolves {done: true} once removed and the engine is back, {done: false} if cancelled. */
  removeKey: () => ipcRenderer.invoke('iris-app:remove-key'),
})
