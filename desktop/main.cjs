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
  nativeTheme,
  protocol,
  safeStorage,
  session,
  shell,
  systemPreferences,
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

const PACKAGED = app.isPackaged
const REPO = path.resolve(__dirname, '..')
const PAGES = path.join(__dirname, 'pages')
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
let quitConfirmed = false
let quitting = false

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

function showMainWindow() {
  if (mainWindow) {
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
  win.once('ready-to-show', () => win.show())
  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null
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
    if (details.reason === 'clean-exit' || mainWindow !== win) return
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

/** IPC from the key screen only: the key window, showing pages/key.html. */
function fromKeyScreen(event) {
  const url = event.senderFrame?.url ?? ''
  return (
    keyWindow !== null &&
    event.sender === keyWindow.webContents &&
    policy.isDesktopPage(url, PAGES) &&
    new URL(url).pathname.endsWith('/key.html')
  )
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
 * runs with the new key, false if the user cancelled. */
async function changeKey() {
  if (!(await confirmBusy('Change the API key?', 'Iris restarts its engine to use the new key.'))) return false
  if (!(await askForKey(currentKey() ? 'change' : 'add'))) return false
  await restartBackend()
  return true
}

/** Remove the stored key after one question; the engine restarts without it. Resolves true once
 * removed, false if the user cancelled. */
async function removeKey() {
  const busy = await busySessions()
  const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
    type: 'warning',
    buttons: ['Remove Key', 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    message: 'Remove the OpenAI key?',
    detail:
      'Iris can’t make notes until you add a key again. Iris’s engine restarts to stop using it.' +
      (busy.length ? `\n\n${busyDetail(busy)}` : ''),
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

// ---- backend ---------------------------------------------------------------------------------

let startCount = 0 // which startBackend() call is the latest

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
    return offerRestart("Iris's engine didn't start.", err.message)
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

/** Sessions recording or making notes right now. */
async function busySessions() {
  const sessions = await backend.getJson('/api/sessions')
  if (!Array.isArray(sessions)) return []
  return sessions.filter((s) => s.status === 'recording' || s.status === 'processing')
}

/** If a session is recording or processing, ask before stopping the engine. */
async function confirmBusy(message, detail, button = 'Continue') {
  const busy = await busySessions()
  if (!busy.length) return true
  const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
    type: 'warning',
    buttons: [button, 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    message,
    detail: `${detail}\n\n${busyDetail(busy)}`,
  })
  return response === 0
}

/** "“Lecture 3” is still recording or making notes…", for a dialog that would stop the engine. */
function busyDetail(busy) {
  const names = busy.slice(0, 3).map((s) => `“${s.title || 'Untitled session'}”`).join(', ')
  return (
    `${names}${busy.length > 3 ? ' and others' : ''} ${busy.length === 1 ? 'is' : 'are'} ` +
    'still recording or making notes. What is recorded so far is kept: resume it from the library later.'
  )
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

  // The UI's getDisplayMedia call. On macOS 15+ the system picker answers it (the user picks a
  // screen or window) and this handler isn't called; otherwise it offers the first screen. System
  // audio comes as 'loopback' either way.
  ses.setDisplayMediaRequestHandler(
    async (request, callback) => {
      const from = request.frame?.url ?? request.securityOrigin
      const deny = () => {
        try {
          callback(null)
        } catch (err) {
          log(`[capture] deny failed: ${err.message}`)
        }
      }
      if (!policy.isIrisUrl(from)) {
        log(`[capture] refused a capture request from ${from}`)
        return deny()
      }
      try {
        const sources = await desktopCapturer.getSources({ types: ['screen'] })
        if (!sources.length) return deny()
        callback({ video: sources[0], audio: request.audioRequested ? 'loopback' : undefined })
      } catch (err) {
        log(`[capture] getSources failed: ${err} (screen permission: ${systemPreferences.getMediaAccessStatus('screen')})`)
        deny()
      }
    },
    { useSystemPicker: true },
  )
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
    quitAsk = confirmBusy('Quit Iris?', 'Quitting stops Iris’s engine.', 'Quit')
      .then((ok) => {
        if (!ok) return
        quitConfirmed = true
        app.quit()
      })
      .finally(() => (quitAsk = null))
  })

  // Windows are closed by now: stop the engine, then quit for real.
  app.on('will-quit', (event) => {
    quitting = true
    if (!backend.running) return
    event.preventDefault()
    backend.stop().finally(() => app.quit())
  })
}
