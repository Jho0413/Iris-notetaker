// Iris desktop app: the main process (docs/architecture/desktop.md).
//
//   npm start      development: the UI from frontend/dist (build it first: `npm run build` in
//                  frontend/, or `npm run start:fresh` here), the backend from the repo with uv
//   make desktop   the packaged app (Iris.app and a .dmg in desktop/dist/)
//
// The window loads app://iris/. The app:// handler (src/handler.cjs) serves the built UI and
// forwards /api to the backend with a per-launch token, so the page never sees the token or the
// backend's port. The backend runs as a child process (src/backend.cjs) and exits by itself if
// this process dies. The OpenAI key lives encrypted in userData (src/keystore.cjs).

const {
  app,
  BrowserWindow,
  Menu,
  desktopCapturer,
  dialog,
  ipcMain,
  nativeImage,
  nativeTheme,
  protocol,
  safeStorage,
  screen,
  session,
  shell,
  systemPreferences,
  Tray,
  webContents,
} = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const { Backend, backendCommand } = require('./src/backend.cjs')
const { registerScheme, handleAppProtocol } = require('./src/handler.cjs')
const { checkKeyWithOpenAI } = require('./src/keycheck.cjs')
const { createKeyStore } = require('./src/keystore.cjs')
const { saveNewKey } = require('./src/keysave.cjs')
const { createAppState } = require('./src/appstate.cjs')
const { WELCOME_SHOWN, shouldWelcome } = require('./src/welcome.cjs')
const policy = require('./src/policy.cjs')
const { ORIGIN } = require('./src/routes.cjs')
const { unloadPrompt } = require('./src/unload.cjs')
const { pdfFileName, PDF_OPTIONS } = require('./src/pdf.cjs')
const { busyDetail, busyKeyRefusal, busyNow, quitDetail, stopRecordings: stopEach } = require('./src/busy.cjs')
const { refusedDebugSwitch } = require('./src/debugging.cjs')
const trayRules = require('./src/tray.cjs')
const pickerRules = require('./src/picker.cjs')

const PACKAGED = app.isPackaged
const REPO = path.resolve(__dirname, '..')
const PAGES = path.join(__dirname, 'pages')
const ASSETS = path.join(__dirname, 'assets')
const DIST = PACKAGED ? path.join(process.resourcesPath, 'frontend') : path.join(REPO, 'frontend', 'dist')
const UI_URL = `${ORIGIN}/`

// Development keeps its own userData (key, logs), apart from the installed app's.
if (!PACKAGED) app.setPath('userData', path.join(app.getPath('appData'), 'Iris Dev'))
const USER_DATA = app.getPath('userData')
const LOGS = path.join(USER_DATA, 'logs')

registerScheme(protocol) // before ready

// ---- logging ---------------------------------------------------------------------------------

fs.mkdirSync(LOGS, { recursive: true })
const mainLog = fs.createWriteStream(path.join(LOGS, 'main.log'), { flags: 'a' })
function log(line) {
  const stamped = `${new Date().toISOString()} ${line}`
  console.log(stamped)
  mainLog.write(stamped + '\n')
}

// The packaged app never runs with remote debugging (src/debugging.cjs): it exits before any
// window or engine. At once, with the log line written synchronously: Chromium opens the debugging
// port as soon as the event loop runs. CommonJS allows this top-level return.
const debugSwitch = refusedDebugSwitch({ packaged: PACKAGED, hasSwitch: (name) => app.commandLine.hasSwitch(name) })
if (debugSwitch) {
  const line = `${new Date().toISOString()} [app] refusing to start with --${debugSwitch}: Iris doesn't run with remote debugging`
  console.error(line)
  fs.appendFileSync(path.join(LOGS, 'main.log'), line + '\n')
  process.exit(1)
  return
}

// ---- state -----------------------------------------------------------------------------------

const keystore = createKeyStore({ safeStorage, file: path.join(USER_DATA, 'openai-key.bin') })
const appState = createAppState(path.join(USER_DATA, 'app-state.json')) // flags such as welcomeShown
// Decrypted once per launch: each decrypt reads the Keychain, and an app whose signature changed
// (every rebuild, for an ad-hoc signed app) makes macOS ask the user each time.
// A failed load is remembered too: a key macOS won't decrypt (e.g. Deny on the Keychain prompt)
// isn't asked for again at each engine start in the same launch.
let apiKey = null
let keyLoaded = false
function currentKey() {
  if (!keyLoaded) {
    apiKey = keystore.load()
    keyLoaded = true
  }
  return apiKey
}
const backend = new Backend({
  command: (port, token) =>
    backendCommand({
      packaged: PACKAGED,
      resourcesPath: process.resourcesPath,
      repoDir: REPO,
      userData: USER_DATA,
      port,
      token,
      parentPid: process.pid,
      apiKey: currentKey(),
      env: process.env,
    }),
  logFile: path.join(LOGS, 'backend.log'),
  log,
})

let mainWindow = null
let keyWindow = null
let keyPrompt = null // {resolve} while the key screen is open
let picker = null // {win, list, sources, resolve} while the picker is open (one at a time)
let quitConfirmed = false
let quitting = false
let menuBar = trayRules.RELAY_START // the menu bar's relay to the Live page (src/tray.cjs)

// ---- windows ---------------------------------------------------------------------------------

const WEB_PREFERENCES = {
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webSecurity: true,
  // Recording happens with Iris in the background: don't throttle its 1 fps frame ticker.
  backgroundThrottling: false,
}

function background() {
  return nativeTheme.shouldUseDarkColors ? '#0e0f13' : '#f6f6f9'
}

/** Show the main window, making it if it was closed. With `show: false` it is only made if
 * needed, and left as it is: a new one stays hidden, an open one where it is. */
function showMainWindow({ show = true } = {}) {
  if (mainWindow) {
    if (!show) return mainWindow
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
    return mainWindow
  }
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 520,
    title: 'Iris',
    show: false,
    backgroundColor: background(),
    // The UI's bridge to the app: two key actions, checked per call (policy.fromIrisWindow).
    webPreferences: { ...WEB_PREFERENCES, preload: path.join(PAGES, 'preload-main.cjs') },
  })
  mainWindow = win
  if (show) win.once('ready-to-show', () => win.show())
  // While recording, closing the window would end it: hide the window instead. The menu bar
  // shows the recording, stops it and shows the window again. Quitting closes it for real.
  win.on('close', (event) => {
    if (quitConfirmed || !trayRules.hideOnClose(menuBar.recording)) return
    event.preventDefault()
    // Hiding a fullscreen window would leave its empty Space on screen: leave fullscreen first.
    if (!win.isFullScreen()) return win.hide()
    win.once('leave-full-screen', () => win.hide())
    win.setFullScreen(false)
  })
  win.on('closed', () => {
    if (mainWindow !== win) return
    mainWindow = null
    menuBarEvent({ type: 'windowClosed' }) // closed before the UI loaded: a start is cancelled
  })
  // A reload or a new page (the starting screen, then the UI): what the old page reported, and
  // its subscription, went with it. Client-side routes are same-document and keep both.
  win.webContents.on('did-start-navigation', ({ isMainFrame, isSameDocument }) => {
    if (isMainFrame && !isSameDocument && mainWindow === win) menuBarEvent({ type: 'pageGone' })
  })
  // A page refusing to unload (beforeunload): the live page while it records, Settings with an
  // unsaved change. Electron would cancel the close or reload silently, so ask here
  // (src/unload.cjs picks the question by page).
  win.webContents.on('will-prevent-unload', (event) => {
    const prompt = unloadPrompt(win.webContents.getURL(), { quitting: quitConfirmed })
    if (!prompt) return event.preventDefault()
    const { leave, ...options } = prompt
    if (dialog.showMessageBoxSync(win, options) === leave) return event.preventDefault()
    // Kept: the window stays open, which cancels a quit in progress, so the next one asks again.
    quitConfirmed = false
  })
  const crashes = []
  win.webContents.on('render-process-gone', async (_e, details) => {
    log(`[window] renderer gone: ${details.reason}`)
    if (mainWindow === win) menuBarEvent({ type: 'pageGone' })
    if (details.reason === 'clean-exit' || mainWindow !== win) return
    // A crashed page sends nothing as it goes (no pagehide): stop what it was recording.
    void stopRecordings('the window crashed')
    const now = Date.now()
    crashes.push(now)
    while (crashes.length && now - crashes[0] > 60_000) crashes.shift()
    if (crashes.length <= 2) return loadUi(win) // reload at once, twice a minute at most
    const { response } = await dialog.showMessageBox(win, {
      type: 'error',
      buttons: ['Reload', 'Quit'],
      defaultId: 0,
      cancelId: 1,
      message: 'The Iris window keeps stopping.',
      detail: `It stopped ${crashes.length} times in a minute (${details.reason}). Your sessions are kept.\n\nLogs: ${LOGS}`,
    })
    if (response === 1) return app.quit()
    crashes.length = 0
    if (!win.isDestroyed()) loadUi(win)
  })
  loadUi(win)
  return win
}

/** The UI once the backend is up, else the starting page (startBackend loads the UI later). */
function loadUi(win) {
  if (backend.info()) win.loadURL(UI_URL)
  else win.loadFile(path.join(PAGES, 'starting.html'))
}

function showingUi(win) {
  return policy.isIrisUrl(win.webContents.getURL())
}

// ---- API key ---------------------------------------------------------------------------------

/** Show the key screen. Resolves true once a key is saved, false if closed without one. */
function askForKey(mode) {
  if (keyWindow) {
    keyWindow.focus()
    return keyPrompt.promise
  }
  let resolve
  const promise = new Promise((r) => (resolve = r))
  keyPrompt = { resolve, promise }
  keyWindow = new BrowserWindow({
    // Sized by its content area, with room for the longest message under the field (two lines),
    // so the window never scrolls: about 460px of content plus the page's 64px of padding.
    width: 560,
    height: 560,
    useContentSize: true,
    resizable: false,
    minimizable: false,
    fullscreenable: false,
    title: 'OpenAI API key',
    show: false,
    backgroundColor: background(),
    parent: mode !== 'first' && mainWindow ? mainWindow : undefined,
    modal: mode !== 'first' && Boolean(mainWindow),
    webPreferences: { ...WEB_PREFERENCES, preload: path.join(PAGES, 'preload-key.cjs') },
  })
  const win = keyWindow
  win.once('ready-to-show', () => win.show())
  win.on('closed', () => {
    if (keyWindow === win) keyWindow = null
    const prompt = keyPrompt
    keyPrompt = null
    prompt?.resolve(false) // no-op if a save resolved it already
  })
  win.loadFile(path.join(PAGES, 'key.html'), { query: { mode } })
  return promise
}

/** IPC from one of the app's own pages (`page`, e.g. key.html) showing in `win`, only. */
function fromDesktopPage(event, win, page) {
  const url = event.senderFrame?.url ?? ''
  return Boolean(win) && event.sender === win.webContents && policy.isDesktopPage(url, PAGES) && new URL(url).pathname.endsWith(`/${page}`)
}

function fromKeyScreen(event) {
  return fromDesktopPage(event, keyWindow, 'key.html')
}

ipcMain.handle('iris-key:has', (event) => {
  if (!fromKeyScreen(event)) throw new Error('not allowed')
  return keystore.has()
})

ipcMain.handle('iris-key:save', async (event, key) => {
  if (!fromKeyScreen(event)) throw new Error('not allowed')
  // The window and prompt that asked: the check below can take seconds, and if that window is
  // closed meanwhile (the user cancelled) nothing is stored, nor does a newer window get answered.
  const win = keyWindow
  const prompt = keyPrompt
  // Checked once with OpenAI before it is stored: a wrong key is caught here, not by a session.
  const result = await saveNewKey(key, {
    check: checkKeyWithOpenAI,
    stillOpen: () => keyWindow === win && !win.isDestroyed(),
    store: (checked) => keystore.save(checked),
  })
  if (!result.ok) {
    if (result.cancelled) log('[key] the key window was closed during the check; nothing saved')
    else if (result.reason) log(`[key] a new API key did not pass the check with OpenAI (${result.reason}); not saved`)
    return { ok: false, error: result.error }
  }
  apiKey = result.key
  keyLoaded = true
  log('[key] saved a new API key')
  prompt?.resolve(true)
  win?.close()
  return { ok: true }
})

ipcMain.handle('iris-key:cancel', (event) => {
  if (!fromKeyScreen(event)) throw new Error('not allowed')
  keyWindow?.close()
})

/** Add or replace the key (menu, Settings, the no-key banner). Resolves true once the engine
 * runs with the new key, false if the user cancelled or a session is busy. */
async function changeKey() {
  const mode = currentKey() ? 'change' : 'add'
  if (await refuseKeyWhileBusy(mode)) return false
  if (!(await askForKey(mode))) return false
  await restartBackend()
  return true
}

/** Remove the stored key after one question; the engine restarts without it. Resolves true once
 * removed, false if the user cancelled or a session is busy. */
async function removeKey() {
  if (await refuseKeyWhileBusy('remove')) return false
  const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
    type: 'warning',
    buttons: ['Remove Key', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    message: 'Remove the OpenAI key?',
    detail: 'Iris can’t make notes until you add a key again.',
  })
  if (response !== 0) return false
  keystore.clear()
  apiKey = null
  keyLoaded = true
  log('[key] removed the API key')
  await restartBackend()
  return true
}

ipcMain.handle('iris-app:change-key', async (event) => {
  if (!policy.fromIrisWindow(event, mainWindow)) throw new Error('not allowed')
  return { done: await changeKey() }
})

ipcMain.handle('iris-app:remove-key', async (event) => {
  if (!policy.fromIrisWindow(event, mainWindow)) throw new Error('not allowed')
  return { done: await removeKey() }
})

// Export PDF: the page as printed (its print stylesheet), saved where the user chooses.
ipcMain.handle('iris-app:save-pdf', async (event, title) => {
  if (!policy.fromIrisWindow(event, mainWindow)) throw new Error('not allowed')
  const win = mainWindow
  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    defaultPath: path.join(app.getPath('downloads'), pdfFileName(title)),
    filters: [{ name: 'PDF', extensions: ['pdf'] }],
  })
  if (canceled || !filePath) return { done: false }
  try {
    const data = await win.webContents.printToPDF(PDF_OPTIONS)
    await fs.promises.writeFile(filePath, data)
    log(`[pdf] saved ${data.length} bytes`)
    return { done: true }
  } catch (err) {
    log(`[pdf] couldn't save: ${err.message}`) // the page says it in its own words
    throw err
  }
})

// ---- picker: what to record ------------------------------------------------------------------

// The Live page's getDisplayMedia call is answered by Iris's own picker (pages/picker.html), not
// the macOS one: it puts the entire screen first and never offers Iris's own windows (a hidden
// window stops being captured). What it lists is src/picker.cjs; this opens it and hands back
// the choice. Thumbnails: a window card at Retina's 2x, about 1.5x for a lone screen card; larger
// ones make each opening slower and the JPEGs sent to the page heavier.
const THUMBNAIL_SIZE = { width: 640, height: 400 }

/** The picker's list, a snapshot: sources from desktopCapturer, thumbnails as data: URLs. */
async function pickerSnapshot() {
  const found = await desktopCapturer.getSources({ types: ['screen', 'window'], thumbnailSize: THUMBNAIL_SIZE, fetchWindowIcons: true })
  const list = pickerRules.listSources(
    found.map((s) => ({ id: s.id, name: s.name, displayId: s.display_id, hasThumbnail: !s.thumbnail.isEmpty() })),
    {
      ownWindowIds: BrowserWindow.getAllWindows().map((w) => w.getMediaSourceId()),
      displays: screen.getAllDisplays(),
      primaryDisplayId: screen.getPrimaryDisplay().id,
    },
  )
  const byId = new Map(found.map((s) => [s.id, s]))
  const image = (img) => (img && !img.isEmpty() ? img : null)
  // JPEG: a PNG of each thumbnail would be several times larger to send to the page.
  const thumbnail = (id) => {
    const img = image(byId.get(id).thumbnail)
    return img && `data:image/jpeg;base64,${img.toJPEG(80).toString('base64')}`
  }
  return {
    list,
    sources: byId,
    page: {
      granted: true,
      screens: list.screens.map((s) => ({ ...s, thumbnail: thumbnail(s.id) })),
      windows: list.windows.map((w) => ({ ...w, thumbnail: thumbnail(w.id), icon: image(byId.get(w.id).appIcon)?.toDataURL() ?? null })),
      selected: pickerRules.defaultSelection(list),
    },
  }
}

/** Close the picker without a choice, also while it is still listing (no window yet). */
function closePicker() {
  const state = picker
  if (!state) return
  state.resolve(null)
  if (state.win) state.win.close()
  else picker = null
}

/** Ask what to record. Resolves the chosen source, or null if cancelled or not allowed. */
async function pickSource() {
  const state = { win: null, list: null, sources: null, page: null, resolve: null }
  picker = state
  const chosen = new Promise((resolve) => (state.resolve = resolve))
  // Without the Screen Recording permission getSources rejects, and the picker says how to allow
  // it. Asked even then: it is Iris's only request for the permission, which is what lists Iris
  // in System Settings. A Mac that never asked reports "denied" as well (measured, macOS 26).
  try {
    Object.assign(state, await pickerSnapshot())
  } catch (err) {
    log(`[capture] couldn't list screens and windows: ${err} (screen permission: ${systemPreferences.getMediaAccessStatus('screen')})`)
    state.page = { granted: false }
  }
  if (picker !== state) return chosen // cancelled while listing (closePicker)

  const win = new BrowserWindow({
    width: 760,
    height: 620,
    minWidth: 480,
    minHeight: 400,
    useContentSize: true,
    minimizable: false,
    fullscreenable: false,
    title: 'Record with Iris',
    show: false,
    backgroundColor: background(),
    webPreferences: { ...WEB_PREFERENCES, preload: path.join(PAGES, 'preload-picker.cjs') },
  })
  state.win = win
  // Started from the menu bar, Iris may not be the active app: bring the picker to the front, and
  // give it the keyboard once macOS has made Iris active (that comes later), so Return records.
  const focusPicker = () => win.isDestroyed() || win.focus()
  win.once('ready-to-show', () => {
    win.show()
    app.once('did-become-active', focusPicker)
    app.focus({ steal: true })
    win.focus()
  })
  win.on('closed', () => {
    app.removeListener('did-become-active', focusPicker)
    if (picker === state) picker = null
    state.resolve(null) // closed without a choice: cancelled (a no-op after a choice)
  })
  win.loadFile(path.join(PAGES, 'picker.html'))
  return chosen
}

function fromPicker(event) {
  return fromDesktopPage(event, picker?.win, 'picker.html')
}

ipcMain.handle('iris-picker:snapshot', (event) => {
  if (!fromPicker(event)) throw new Error('not allowed')
  return picker.page
})

ipcMain.handle('iris-picker:choose', (event, id) => {
  if (!fromPicker(event)) throw new Error('not allowed')
  const state = picker
  const valid = state.list && pickerRules.chosenId(state.list, id)
  if (!valid) throw new Error('not offered')
  state.resolve(state.sources.get(valid))
  state.win.close()
})

ipcMain.handle('iris-picker:cancel', (event) => {
  if (!fromPicker(event)) throw new Error('not allowed')
  picker.win.close()
})

ipcMain.handle('iris-picker:open-settings', (event) => {
  if (!fromPicker(event)) throw new Error('not allowed')
  shell.openExternal(pickerRules.SCREEN_SETTINGS_URL)
})

// ---- menu bar --------------------------------------------------------------------------------

// The menu bar icon starts and stops a recording, but recording happens in the Live page (its
// capture code runs in the window), so the icon only relays: "Record Audio" or "Record Screen…"
// asks the page to start (making the window, hidden, if it was closed), and only the picker
// shows (none for audio); Share Screen… and Stop Sharing turn the page's screen on and off (only
// the picker shows); Stop asks the page to stop, which makes notes as its own Stop does.
let tray = null
let startAnswerTimer = null
let trayTicker = null
const TRAY_ICONS = {}

function trayIcon(recordingNow) {
  const name = recordingNow ? 'trayRecording.png' : 'trayTemplate.png' // "Template": macOS tints it
  TRAY_ICONS[name] ??= nativeImage.createFromPath(path.join(ASSETS, name))
  return TRAY_ICONS[name]
}

function createTray() {
  tray = new Tray(trayIcon(false))
  tray.setToolTip('Iris')
  showTray()
}

/** Start recording from the menu bar, `mode` 'audio' or 'screen'. */
function recordFromMenuBar(mode) {
  // Only the picker shows (none for audio): bringing the window up would switch to its Space,
  // and the picker would list that Space's windows instead of the ones the user is looking at.
  // The window is made if it was closed (the recording runs in its Live page); the start waits
  // for its UI.
  showMainWindow({ show: false })
  menuBarEvent({ type: 'record', mode })
}

const TRAY_ACTIONS = {
  recordAudio: () => recordFromMenuBar('audio'),
  recordScreen: () => recordFromMenuBar('screen'),
  shareScreen: () => menuBarEvent({ type: 'screen', on: true }),
  stopSharing: () => menuBarEvent({ type: 'screen', on: false }),
  stop: () => menuBarEvent({ type: 'stop' }),
  continue: () => menuBarEvent({ type: 'choice', choice: 'continue' }),
  makeNotes: () => menuBarEvent({ type: 'choice', choice: 'makeNotes' }),
  startOver: () => menuBarEvent({ type: 'choice', choice: 'startOver' }),
  discard: () => menuBarEvent({ type: 'choice', choice: 'discard' }),
  show: () => showMainWindow(),
  quit: () => app.quit(),
}

/** The icon, the time beside it and the menu for what the Live page last reported. */
function showTray() {
  if (!tray) return
  const view = trayRules.trayView(menuBar.recording, Date.now())
  tray.setImage(trayIcon(view.recording))
  tray.setTitle(view.title, { fontType: 'monospacedDigit' })
  tray.setContextMenu(
    Menu.buildFromTemplate(view.items.map(({ id, ...item }) => (id ? { ...item, click: TRAY_ACTIONS[id] } : item))),
  )
  // Only the time changes each second; the menu is rebuilt when the phase does.
  if (menuBar.recording.phase === 'recording') {
    trayTicker ??= setInterval(
      () => tray.setTitle(trayRules.trayView(menuBar.recording, Date.now()).title, { fontType: 'monospacedDigit' }),
      1000,
    )
  } else if (trayTicker) {
    clearInterval(trayTicker)
    trayTicker = null
  }
}

/** Apply a relay event (src/tray.cjs) and carry out what it asks for. */
function menuBarEvent(event) {
  const before = menuBar.recording
  const { state, send, show, malformed, closePicker: pickerAbandoned } = trayRules.relay(menuBar, event)
  menuBar = state
  if (malformed) log('[tray] ignored a malformed recording report')
  if (pickerAbandoned) closePicker()
  if (show) showMainWindow()
  if (send && mainWindow) {
    log(`[tray] ${send} recording`)
    mainWindow.webContents.send('iris-app:record', send)
    // A page that doesn't start (held up by a question it asks) gets its window shown.
    if (send === 'start-audio' || send === 'start-screen') {
      clearTimeout(startAnswerTimer)
      startAnswerTimer = setTimeout(() => menuBarEvent({ type: 'startUnanswered' }), trayRules.START_ANSWER_MS)
    }
  }
  if (menuBar.recording !== before) showTray()
}

ipcMain.on('iris-app:record-listening', (event) => {
  if (policy.fromIrisWindow(event, mainWindow)) menuBarEvent({ type: 'listening' })
})

ipcMain.on('iris-app:recording', (event, report) => {
  if (policy.fromIrisWindow(event, mainWindow)) menuBarEvent({ type: 'report', report })
})

// ---- backend ---------------------------------------------------------------------------------

let startCount = 0 // which startBackend() call is the latest
let launchChecked = false // the first engine start of this launch has stopped leftover recordings

/** Start the engine unless it is up (a start under way is joined, never doubled). */
async function startBackend() {
  if (backend.info()) return showUiWhenReady()
  const mine = ++startCount
  try {
    await backend.start()
  } catch (err) {
    // Stopped on purpose (quit, restart) or superseded by a newer start: nothing to report.
    if (quitting || err.stopped || mine !== startCount) return
    log(`[backend] failed to start: ${err.message}`)
    menuBarEvent({ type: 'engineFailed' })
    return offerRestart("Iris's engine didn't start.", err.message)
  }
  // Only on this launch's first start, before the UI loads: a restart (Restart Engine, a key
  // change, the crash dialog) leaves the Live page recording, its uploads retried meanwhile.
  if (!launchChecked) {
    launchChecked = true
    await stopRecordings('left by an earlier launch')
  }
  showUiWhenReady()
}

function showUiWhenReady() {
  if (mainWindow && backend.info() && !showingUi(mainWindow)) mainWindow.loadURL(UI_URL)
}

async function restartBackend() {
  await backend.stop()
  await startBackend()
}

backend.on('exit', ({ expected, duringStart }) => {
  // A start that fails is reported by startBackend() instead.
  if (!expected && !duringStart && !quitting) {
    offerRestart("Iris's engine stopped unexpectedly.", 'What you recorded so far is kept.')
  }
})

let restartDialog = null // at most one "engine stopped" dialog at a time

function offerRestart(message, detail) {
  restartDialog ??= askToRestart(message, detail).finally(() => (restartDialog = null))
  return restartDialog
}

async function askToRestart(message, detail) {
  for (;;) {
    const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
      type: 'error',
      buttons: ['Restart', 'Show Logs', 'Quit'],
      defaultId: 0,
      cancelId: 2,
      message,
      detail: `${detail}\n\nLogs: ${LOGS}`,
    })
    if (response === 0) {
      restartDialog = null // a failure of this restart may ask again
      return startBackend()
    }
    if (response === 2) return app.quit()
    shell.openPath(LOGS)
  }
}

/**
 * Stop every session still "recording" (src/busy.cjs). Called whenever no page of this app can be
 * recording: at launch, at quit before the engine stops (the page's own stop request, sent as its
 * window closes, races the engine's shutdown), and when the window's page crashes.
 */
function stopRecordings(why) {
  return stopEach(backend, why, log)
}

/** Sessions recording or making notes right now. */
async function busySessions() {
  const capturing = trayRules.holdsRecording(menuBar.recording) // what the Live page reported
  return busyNow(await backend.getJson('/api/sessions'), { capturing })
}

/** If a session is recording or processing, ask before stopping the engine. `detail`, if any,
 * comes before what `describe` says about the busy sessions. */
async function confirmBusy(message, detail, button = 'Continue', describe = busyDetail) {
  const busy = await busySessions()
  if (!busy.length) return true
  const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
    type: 'warning',
    buttons: [button, 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    message,
    detail: detail ? `${detail}\n\n${describe(busy)}` : describe(busy),
  })
  return response === 0
}

/** A key change or removal restarts the engine: while a session is busy, say so (OK only) and
 * resolve true. Restart Engine and Quit still only ask (confirmBusy): they are the way out of a
 * stuck engine. */
async function refuseKeyWhileBusy(action) {
  const busy = await busySessions()
  if (!busy.length) return false
  log(`[key] refused to ${action} the key: ${busy.length} session(s) busy`)
  await dialog.showMessageBox(mainWindow ?? undefined, busyKeyRefusal(busy, action))
  return true
}

// ---- security --------------------------------------------------------------------------------

/** "Open full size" on a figure: the file in its own window (no preload; the guards apply). */
function openFigure(url) {
  const win = new BrowserWindow({
    width: 1200,
    height: 900,
    title: 'Iris figure',
    backgroundColor: background(),
    webPreferences: WEB_PREFERENCES,
  })
  win.loadURL(url)
}

function openOutside(url) {
  const external = policy.externalUrl(url)
  if (external) shell.openExternal(external)
}

// Every window shows only Iris's own UI (or the app's own pages, loaded by this process); links
// open in the browser.
app.on('web-contents-created', (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (policy.isSessionFileUrl(url)) openFigure(url)
    else openOutside(url)
    return { action: 'deny' }
  })
  const guard = (event, url) => {
    if (policy.isIrisUrl(url)) return
    event.preventDefault()
    openOutside(url)
  }
  contents.on('will-navigate', guard)
  contents.on('will-redirect', guard)
  contents.on('will-attach-webview', (event) => event.preventDefault())
})

function secureSession(ses) {
  ses.setPermissionRequestHandler((_wc, permission, callback, details) => {
    const allowed = policy.permissionAllowed(permission, details.requestingUrl, details)
    if (!allowed) log(`[permissions] refused ${permission} for ${details.requestingUrl}`)
    callback(allowed)
  })
  ses.setPermissionCheckHandler((_wc, permission, origin) => policy.permissionCheckAllowed(permission, origin))

  // The UI's getDisplayMedia calls. A request for sound alone gets the Mac's sound with no picker:
  // the Live page records it throughout, whether or not the screen is shared. A request for a
  // picture (the screen turned on) gets Iris's picker (see "picker" above), and the picture alone
  // unless sound was asked for too (then all of the Mac's sound as 'loopback'). A refusal reaches
  // the page as a cancelled share.
  ses.setDisplayMediaRequestHandler(async (request, callback) => {
    const from = request.frame?.url ?? request.securityOrigin
    const answer = (streams) => {
      try {
        callback(streams)
      } catch (err) {
        log(`[capture] answering the page failed: ${err.message}`) // e.g. the page went away
      }
    }
    if (!policy.isIrisUrl(from)) {
      log(`[capture] refused a capture request from ${from}`)
      return answer(null)
    }
    if (pickerRules.soundOnly(request)) {
      log("[capture] recording the Mac's sound")
      return answer({ audio: 'loopback' })
    }
    // One picker at a time. Chromium holds a second request from the same page until the first
    // is answered (measured, Electron 45); any other request meanwhile focuses the open picker.
    if (picker) {
      picker.win?.focus()
      return answer(null)
    }
    // The page that asked going away (its window closed, a reload, a crash) closes the picker:
    // its request is gone, so a choice would record nothing.
    const requester = request.frame ? webContents.fromFrame(request.frame) : null
    const onNavigation = ({ isMainFrame, isSameDocument }) => isMainFrame && !isSameDocument && closePicker()
    requester?.once('destroyed', closePicker)
    requester?.once('render-process-gone', closePicker)
    requester?.on('did-start-navigation', onNavigation)
    let source = null
    let failed = false
    try {
      source = await pickSource()
    } catch (err) {
      failed = true
      log(`[capture] the picker failed: ${err.stack ?? err}`)
      closePicker()
    } finally {
      requester?.removeListener('destroyed', closePicker)
      requester?.removeListener('render-process-gone', closePicker)
      requester?.removeListener('did-start-navigation', onNavigation)
    }
    if (!source) {
      if (!failed) {
        log('[capture] nothing chosen to record')
        // Before the page hears of it: a start from the menu bar then shows no window for it.
        menuBarEvent({ type: 'pickerCancelled' })
      }
      return answer(null)
    }
    log(`[capture] recording ${source.id}`)
    answer({ video: source, audio: request.audioRequested ? 'loopback' : undefined })
  })
}

// ---- menu ------------------------------------------------------------------------------------

function buildMenu() {
  const template = [
    {
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { label: 'Change OpenAI API Key…', click: () => void changeKey() },
        { label: 'Show Logs', click: () => shell.openPath(LOGS) },
        {
          label: 'Restart Engine',
          click: async () => {
            if (await confirmBusy('Restart the engine?', 'Iris stops and starts its engine.', 'Restart')) {
              await restartBackend()
            }
          },
        },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    { role: 'windowMenu' },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

// ---- lifecycle -------------------------------------------------------------------------------

if (!app.requestSingleInstanceLock()) {
  // Another Iris is running: it focuses its window (second-instance), this one leaves.
  app.quit()
} else {
  app.on('second-instance', () => {
    if (app.isReady() && !keyWindow) showMainWindow()
    else keyWindow?.focus()
  })

  app.whenReady().then(async () => {
    log(`[app] starting ${app.getVersion()} (${PACKAGED ? 'packaged' : 'development'}), data in ${USER_DATA}`)
    secureSession(session.defaultSession)
    handleAppProtocol({ protocol: session.defaultSession.protocol, distDir: DIST, backend: () => backend.info(), log })
    buildMenu()
    createTray()

    // The packaged app welcomes a new install once (Welcome, then Connect), never again; in
    // development the repo's backend/.env can supply the key. Without a key Iris still opens: the
    // Library and Settings say notes can't be made, with Add key….
    if (PACKAGED) {
      const hasKey = Boolean(currentKey())
      if (shouldWelcome({ hasKey, seen: appState.get(WELCOME_SHOWN) === true })) {
        appState.set(WELCOME_SHOWN, true) // before asking: closing or quitting from the welcome counts too
        if (!(await askForKey('first'))) log('[key] set up later: opening without an API key')
      } else if (!hasKey) {
        log(keystore.has() ? '[key] the stored key could not be decrypted: opening without one' : '[key] no API key: opening without one')
      }
      if (hasKey) appState.set(WELCOME_SHOWN, true) // an install that already has a key is never welcomed
    }
    showMainWindow()
    await startBackend()
  })

  // macOS: closing the window keeps Iris (and notes being made) running; the Dock icon reopens it.
  app.on('window-all-closed', () => {})
  app.on('activate', () => {
    if (app.isReady() && !keyWindow) showMainWindow()
  })

  let quitAsk = null // the pending "Quit Iris?" check: another Cmd-Q waits for it
  app.on('before-quit', (event) => {
    if (quitConfirmed) return
    event.preventDefault()
    if (quitAsk) return
    // The sessions alone: the engine is Iris's own business, not the user's.
    quitAsk = confirmBusy('Quit Iris?', null, 'Quit', quitDetail)
      .then((ok) => {
        if (!ok) return
        quitConfirmed = true
        app.quit()
      })
      .finally(() => (quitAsk = null))
  })

  // Windows are closed by now: stop the recordings, then the engine, then quit for real.
  let quitStop = null // the stop under way: another will-quit waits for it
  app.on('will-quit', (event) => {
    quitting = true
    if (!backend.running) return
    event.preventDefault()
    quitStop ??= stopRecordings('quitting')
      .then(() => backend.stop())
      .finally(() => {
        quitStop = null // an engine started meanwhile (Restart Engine) is stopped by the next one
        app.quit()
      })
  })
}
