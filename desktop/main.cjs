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
const { execFile } = require('node:child_process')
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
const { bringSharedAppForward } = require('./src/focus.cjs')

const PACKAGED = app.isPackaged
const REPO = path.resolve(__dirname, '..')
const PAGES = path.join(__dirname, 'pages')
const ASSETS = path.join(__dirname, 'assets')
const DIST = PACKAGED ? path.join(process.resourcesPath, 'frontend') : path.join(REPO, 'frontend', 'dist')
const UI_URL = `${ORIGIN}/`

if (!PACKAGED) app.setPath('userData', path.join(app.getPath('appData'), 'Iris Dev'))
const USER_DATA = app.getPath('userData')
const LOGS = path.join(USER_DATA, 'logs')

registerScheme(protocol)

fs.mkdirSync(LOGS, { recursive: true })
const mainLog = fs.createWriteStream(path.join(LOGS, 'main.log'), { flags: 'a' })
function log(line) {
  const stamped = `${new Date().toISOString()} ${line}`
  console.log(stamped)
  mainLog.write(stamped + '\n')
}

const debugSwitch = refusedDebugSwitch({ packaged: PACKAGED, hasSwitch: (name) => app.commandLine.hasSwitch(name) })
if (debugSwitch) {
  const line = `${new Date().toISOString()} [app] refusing to start with --${debugSwitch}: Iris doesn't run with remote debugging`
  console.error(line)
  fs.appendFileSync(path.join(LOGS, 'main.log'), line + '\n')
  process.exit(1)
  return
}

const keystore = createKeyStore({ safeStorage, file: path.join(USER_DATA, 'openai-key.bin') })
const appState = createAppState(path.join(USER_DATA, 'app-state.json'))
function rememberWelcomeShown() {
  const err = appState.set(WELCOME_SHOWN, true)
  if (err) log(`[app] couldn't remember the welcome was shown: ${err.message}`)
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
      apiKey: keystore.load(),
      env: process.env,
    }),
  logFile: path.join(LOGS, 'backend.log'),
  log,
})

let mainWindow = null
let keyWindow = null
let keyPrompt = null
let picker = null
let quitConfirmed = false
let quitting = false
let menuBar = trayRules.RELAY_START

const WEB_PREFERENCES = {
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  webSecurity: true,
  backgroundThrottling: false,
  devTools: !PACKAGED,
}

function newWindow({ preload, ...options }) {
  return new BrowserWindow({
    show: false,
    ...options,
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0e0f13' : '#f6f6f9',
    webPreferences: { ...WEB_PREFERENCES, ...(preload && { preload: path.join(PAGES, preload) }) },
  })
}

function showMainWindow({ show = true } = {}) {
  if (mainWindow) {
    if (!show) return mainWindow
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
    return mainWindow
  }
  const win = newWindow({
    width: 1280,
    height: 860,
    minWidth: 720,
    minHeight: 520,
    title: 'Iris',
    preload: 'preload-main.cjs',
  })
  mainWindow = win
  if (show) win.once('ready-to-show', () => win.show())
  win.on('close', (event) => {
    if (quitConfirmed || !trayRules.hideOnClose(menuBar.recording)) return
    event.preventDefault()
    if (!win.isFullScreen()) return win.hide()
    win.once('leave-full-screen', () => win.hide())
    win.setFullScreen(false)
  })
  win.on('closed', () => {
    if (mainWindow !== win) return
    mainWindow = null
    menuBarEvent({ type: 'windowClosed' })
  })
  win.webContents.on('did-start-navigation', ({ isMainFrame, isSameDocument }) => {
    if (isMainFrame && !isSameDocument && mainWindow === win) menuBarEvent({ type: 'pageGone' })
  })
  win.webContents.on('will-prevent-unload', (event) => {
    const prompt = unloadPrompt(win.webContents.getURL(), { quitting: quitConfirmed })
    if (!prompt) return event.preventDefault()
    const { leave, ...options } = prompt
    if (dialog.showMessageBoxSync(win, options) === leave) return event.preventDefault()
    quitConfirmed = false
  })
  const crashes = []
  win.webContents.on('render-process-gone', async (_e, details) => {
    log(`[window] renderer gone: ${details.reason}`)
    if (mainWindow === win) menuBarEvent({ type: 'pageGone' })
    if (details.reason === 'clean-exit' || mainWindow !== win) return
    void stopRecordings('the window crashed')
    const now = Date.now()
    crashes.push(now)
    while (crashes.length && now - crashes[0] > 60_000) crashes.shift()
    if (crashes.length <= 2) return loadUi(win)
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

function loadUi(win) {
  if (backend.info()) win.loadURL(UI_URL)
  else win.loadFile(path.join(PAGES, 'starting.html'))
}

function askForKey(mode) {
  if (keyWindow) {
    keyWindow.focus()
    return keyPrompt.promise
  }
  let resolve
  const promise = new Promise((r) => (resolve = r))
  keyPrompt = { resolve, promise }
  keyWindow = newWindow({
    width: 560,
    height: 560,
    useContentSize: true,
    resizable: false,
    minimizable: false,
    fullscreenable: false,
    title: 'OpenAI API key',
    parent: mode !== 'first' && mainWindow ? mainWindow : undefined,
    modal: mode !== 'first' && Boolean(mainWindow),
    preload: 'preload-key.cjs',
  })
  const win = keyWindow
  win.once('ready-to-show', () => win.show())
  win.on('closed', () => {
    if (keyWindow === win) keyWindow = null
    const prompt = keyPrompt
    keyPrompt = null
    prompt?.resolve(false)
  })
  win.loadFile(path.join(PAGES, 'key.html'), { query: { mode } })
  return promise
}

function handleFrom(allowed, channel, answer) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!allowed(event)) throw new Error('not allowed')
    return answer(...args)
  })
}

function fromKeyScreen(event) {
  return policy.fromDesktopPage(event, keyWindow, path.join(PAGES, 'key.html'))
}

function fromMainWindow(event) {
  return policy.fromIrisWindow(event, mainWindow)
}

handleFrom(fromKeyScreen, 'iris-key:save', async (key) => {
  const win = keyWindow
  const prompt = keyPrompt
  const result = await saveNewKey(key, {
    check: checkKeyWithOpenAI,
    stillOpen: () => keyWindow === win && !win.isDestroyed(),
    store: keystore.save,
  })
  if (!result.ok) {
    if (result.cancelled) log('[key] the key window was closed during the check; nothing saved')
    else if (result.reason) log(`[key] a new API key did not pass the check with OpenAI (${result.reason}); not saved`)
    return { ok: false, error: result.error }
  }
  log('[key] saved a new API key')
  prompt?.resolve(true)
  win?.close()
  return { ok: true }
})

handleFrom(fromKeyScreen, 'iris-key:cancel', () => keyWindow?.close())

async function changeKey() {
  const mode = keystore.load() ? 'change' : 'add'
  if (await refuseKeyWhileBusy(mode)) return false
  if (!(await askForKey(mode))) return false
  await restartBackend()
  return true
}

async function removeKey() {
  if (await refuseKeyWhileBusy('remove')) return false
  if (!(await confirm('Remove the OpenAI key?', 'Iris can’t make notes until you add a key again.', 'Remove Key'))) return false
  keystore.clear()
  log('[key] removed the API key')
  await restartBackend()
  return true
}

handleFrom(fromMainWindow, 'iris-app:change-key', async () => ({ done: await changeKey() }))

handleFrom(fromMainWindow, 'iris-app:remove-key', async () => ({ done: await removeKey() }))

handleFrom(fromMainWindow, 'iris-app:save-pdf', async (title) => {
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
    log(`[pdf] couldn't save: ${err.message}`)
    throw err
  }
})

const THUMBNAIL_SIZE = { width: 640, height: 400 }

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

function closePicker() {
  const state = picker
  if (!state) return
  state.resolve(null)
  if (state.win) state.win.close()
  else picker = null
}

async function pickSource() {
  const state = { win: null, list: null, sources: null, page: null, resolve: null }
  picker = state
  const chosen = new Promise((resolve) => (state.resolve = resolve))
  try {
    Object.assign(state, await pickerSnapshot())
  } catch (err) {
    log(`[capture] couldn't list screens and windows: ${err} (screen permission: ${systemPreferences.getMediaAccessStatus('screen')})`)
    state.page = { granted: false }
  }
  if (picker !== state) return chosen

  const win = newWindow({
    width: 760,
    height: 620,
    minWidth: 480,
    minHeight: 400,
    useContentSize: true,
    minimizable: false,
    fullscreenable: false,
    title: 'Record with Iris',
    preload: 'preload-picker.cjs',
  })
  state.win = win
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
    state.resolve(null)
  })
  win.loadFile(path.join(PAGES, 'picker.html'))
  return chosen
}

function fromPicker(event) {
  return policy.fromDesktopPage(event, picker?.win, path.join(PAGES, 'picker.html'))
}

handleFrom(fromPicker, 'iris-picker:snapshot', () => picker.page)

handleFrom(fromPicker, 'iris-picker:choose', (id) => {
  const state = picker
  const valid = state.list && pickerRules.chosenId(state.list, id)
  if (!valid) throw new Error('not offered')
  state.resolve(state.sources.get(valid))
  state.win.close()
})

handleFrom(fromPicker, 'iris-picker:cancel', () => picker.win.close())

handleFrom(fromPicker, 'iris-picker:open-settings', () => void shell.openExternal(pickerRules.SCREEN_SETTINGS_URL))

let tray = null
let startAnswerTimer = null
let trayTicker = null
const TRAY_ICONS = {}

function trayIcon(recordingNow) {
  const name = recordingNow ? 'trayRecording.png' : 'trayTemplate.png'
  TRAY_ICONS[name] ??= nativeImage.createFromPath(path.join(ASSETS, name))
  return TRAY_ICONS[name]
}

function createTray() {
  tray = new Tray(trayIcon(false))
  tray.setToolTip('Iris')
  showTray()
}

function recordFromMenuBar(mode) {
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

function showTray() {
  if (!tray) return
  const view = trayRules.trayView(menuBar.recording, Date.now())
  tray.setImage(trayIcon(view.recording))
  tray.setTitle(view.title, { fontType: 'monospacedDigit' })
  tray.setContextMenu(
    Menu.buildFromTemplate(view.items.map(({ id, ...item }) => (id ? { ...item, click: TRAY_ACTIONS[id] } : item))),
  )
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
    if (send === 'start-audio' || send === 'start-screen') {
      clearTimeout(startAnswerTimer)
      startAnswerTimer = setTimeout(() => menuBarEvent({ type: 'startUnanswered' }), trayRules.START_ANSWER_MS)
    }
  }
  if (menuBar.recording !== before) showTray()
}

ipcMain.on('iris-app:record-listening', (event) => {
  if (fromMainWindow(event)) menuBarEvent({ type: 'listening' })
})

ipcMain.on('iris-app:recording', (event, report) => {
  if (fromMainWindow(event)) menuBarEvent({ type: 'report', report })
})

let startCount = 0
let launchChecked = false

async function startBackend() {
  if (backend.info()) return showUiWhenReady()
  const mine = ++startCount
  try {
    await backend.start()
  } catch (err) {
    if (quitting || err.stopped || mine !== startCount) return
    log(`[backend] failed to start: ${err.message}`)
    menuBarEvent({ type: 'engineFailed' })
    return offerRestart("Iris's engine didn't start.", err.message)
  }
  if (!launchChecked) {
    launchChecked = true
    await stopRecordings('left by an earlier launch')
  }
  showUiWhenReady()
}

function showUiWhenReady() {
  if (mainWindow && backend.info() && !policy.isIrisUrl(mainWindow.webContents.getURL())) mainWindow.loadURL(UI_URL)
}

async function restartBackend() {
  await backend.stop()
  await startBackend()
}

backend.on('exit', ({ expected, duringStart }) => {
  if (!expected && !duringStart && !quitting) {
    offerRestart("Iris's engine stopped unexpectedly.", 'What you recorded so far is kept.')
  }
})

let restartDialog = null

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
      restartDialog = null
      return startBackend()
    }
    if (response === 2) return app.quit()
    shell.openPath(LOGS)
  }
}

function stopRecordings(why) {
  return stopEach(backend, why, log)
}

async function busySessions() {
  const capturing = trayRules.holdsRecording(menuBar.recording)
  return busyNow(await backend.getJson('/api/sessions'), { capturing })
}

async function confirmBusy(message, detail, button = 'Continue', describe = busyDetail) {
  const busy = await busySessions()
  if (!busy.length) return true
  return confirm(message, detail ? `${detail}\n\n${describe(busy)}` : describe(busy), button)
}

async function confirm(message, detail, button) {
  const { response } = await dialog.showMessageBox(mainWindow ?? undefined, {
    type: 'warning',
    buttons: [button, 'Cancel'],
    defaultId: 1,
    cancelId: 1,
    message,
    detail,
  })
  return response === 0
}

async function refuseKeyWhileBusy(action) {
  const busy = await busySessions()
  if (!busy.length) return false
  log(`[key] refused to ${action} the key: ${busy.length} session(s) busy`)
  await dialog.showMessageBox(mainWindow ?? undefined, busyKeyRefusal(busy, action))
  return true
}

function openFigure(url) {
  newWindow({ width: 1200, height: 900, title: 'Iris figure', show: true }).loadURL(url)
}

function openOutside(url) {
  const external = policy.externalUrl(url)
  if (external) shell.openExternal(external)
}

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

  ses.setDisplayMediaRequestHandler(async (request, callback) => {
    const from = request.frame?.url ?? request.securityOrigin
    const answer = (streams) => {
      try {
        callback(streams)
      } catch (err) {
        log(`[capture] answering the page failed: ${err.message}`)
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
    if (picker) {
      picker.win?.focus()
      return answer(null)
    }
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
        menuBarEvent({ type: 'pickerCancelled' })
      }
      return answer(null)
    }
    log(`[capture] recording ${source.id}`)
    answer({ video: source, audio: request.audioRequested ? 'loopback' : undefined })
    void bringSharedAppForward(source.id, { execFile, log })
  })
}

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
    { role: 'fileMenu' },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        ...(PACKAGED ? [] : [{ role: 'toggleDevTools' }]),
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

function showIris() {
  if (app.isReady() && !keyWindow) showMainWindow()
  else keyWindow?.focus()
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  app.on('second-instance', showIris)

  app.whenReady().then(async () => {
    log(`[app] starting ${app.getVersion()} (${PACKAGED ? 'packaged' : 'development'}), data in ${USER_DATA}`)
    secureSession(session.defaultSession)
    handleAppProtocol({ protocol: session.defaultSession.protocol, distDir: DIST, backend: () => backend.info(), log })
    buildMenu()
    createTray()

    if (PACKAGED) {
      const hasKey = Boolean(keystore.load())
      if (shouldWelcome({ hasKey, seen: appState.get(WELCOME_SHOWN) === true })) {
        rememberWelcomeShown()
        if (!(await askForKey('first'))) log('[key] set up later: opening without an API key')
      } else if (!hasKey) {
        log(keystore.has() ? '[key] the stored key could not be decrypted: opening without one' : '[key] no API key: opening without one')
      }
      if (hasKey) rememberWelcomeShown()
    }
    showMainWindow()
    await startBackend()
  })

  app.on('window-all-closed', () => {})
  app.on('activate', showIris)

  let quitAsk = null
  app.on('before-quit', (event) => {
    if (quitConfirmed) return
    event.preventDefault()
    if (quitAsk) return
    quitAsk = confirmBusy('Quit Iris?', null, 'Quit', quitDetail)
      .then((ok) => {
        if (!ok) return
        quitConfirmed = true
        app.quit()
      })
      .finally(() => (quitAsk = null))
  })

  let quitStop = null
  app.on('will-quit', (event) => {
    quitting = true
    if (!backend.running) return
    event.preventDefault()
    quitStop ??= stopRecordings('quitting')
      .then(() => backend.stop())
      .finally(() => {
        quitStop = null
        app.quit()
      })
  })
}
