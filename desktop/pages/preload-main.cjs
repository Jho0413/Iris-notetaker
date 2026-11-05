// Preload for the main window: the Iris UI's only way to ask the app for something. Three actions,
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
  /** Save the page, as printed, as a PDF named after `title`, where the user chooses.
   * Resolves {done: true} once saved, {done: false} if cancelled. */
  savePdf: (title) => ipcRenderer.invoke('iris-app:save-pdf', String(title)),
})
