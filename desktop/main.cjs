// Spike: Electron shell around the Iris UI and backend (docs/roadmap.md, "Desktop app"). Needs the
// repo set up (`make setup`, backend/.env); the backend isn't bundled yet.
//
//   npm start          start the backend (uv) and the Vite UI from the repo, open the UI
//   npm run probe      no UI: capture a screen + system audio while a tone plays, log what arrived
//
// Both launch Electron.app with `open`, so macOS attributes Screen Recording to it rather than to
// the terminal. The log is in $TMPDIR/iris-desktop.log.
//
// Screen capture goes through the UI's own getDisplayMedia call. The display-media handler below
// answers it: the macOS system picker where available, else the first screen, with system audio
// ("loopback") on both paths.

const { app, BrowserWindow, desktopCapturer, session, shell, systemPreferences } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const REPO = path.resolve(__dirname, '..')
const PROBE = process.argv.includes('--probe')
const API = 'http://127.0.0.1:8000/api/health'
const UI = 'http://localhost:5173/'

const children = []

// Launched with `open` (so macOS asks for permissions for this app, not the terminal), stdout goes
// nowhere: mirror the log to a file.
const LOG = path.join(os.tmpdir(), 'iris-desktop.log')
fs.writeFileSync(LOG, '')
function log(line) {
  console.log(line)
  fs.appendFileSync(LOG, line + '\n')
}

function start(cmd, args, cwd, tag) {
  // A login shell, so uv and npm are on PATH when launched from Finder or `open`.
  const child = spawn('/bin/zsh', ['-lc', [cmd, ...args].join(' ')], { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
  const out = (buf) => log(buf.toString().trimEnd().replace(/^/gm, `[${tag}] `))
  child.stdout.on('data', out)
  child.stderr.on('data', out)
  child.on('exit', (code) => log(`[${tag}] exited ${code}`))
  children.push(child)
  return child
}

async function waitFor(url, timeoutMs = 60_000) {
  const until = Date.now() + timeoutMs
  while (Date.now() < until) {
    try {
      if ((await fetch(url)).ok) return
    } catch {}
    await new Promise((r) => setTimeout(r, 300))
  }
  throw new Error(`${url} did not come up in ${timeoutMs / 1000}s`)
}

async function isUp(url) {
  try {
    return (await fetch(url)).ok
  } catch {
    return false
  }
}

/** Only the Iris UI (or the probe's local page) may capture the screen. */
function isIris(url) {
  if (PROBE && url?.startsWith('file:')) return true
  try {
    return new URL(url).origin === new URL(UI).origin
  } catch {
    return false
  }
}

function handleDisplayMedia() {
  session.defaultSession.setDisplayMediaRequestHandler(
    async (request, callback) => {
      // Reached when the system picker isn't used (probe, or macOS without it).
      const from = request.frame?.url ?? request.securityOrigin
      if (!isIris(from)) {
        log(`[capture] refused a capture request from ${from}`)
        return callback({})
      }
      try {
        const sources = await desktopCapturer.getSources({ types: ['screen'] })
        log(`[capture] fallback handler: ${sources.length} screen(s), audio requested: ${request.audioRequested}`)
        if (!sources.length) return callback({})
        callback({ video: sources[0], audio: request.audioRequested ? 'loopback' : undefined })
      } catch (err) {
        log(`[capture] getSources failed: ${err} (screen permission: ${systemPreferences.getMediaAccessStatus('screen')})`)
        callback({})
      }
    },
    { useSystemPicker: !PROBE },
  )
}

// ---- probe ---------------------------------------------------------------------------------

function writeTone(file, seconds = 6, hz = 440) {
  const rate = 44100
  const n = rate * seconds
  const buf = Buffer.alloc(44 + n * 2)
  buf.write('RIFF', 0); buf.writeUInt32LE(36 + n * 2, 4); buf.write('WAVE', 8)
  buf.write('fmt ', 12); buf.writeUInt32LE(16, 16); buf.writeUInt16LE(1, 20); buf.writeUInt16LE(1, 22)
  buf.writeUInt32LE(rate, 24); buf.writeUInt32LE(rate * 2, 28); buf.writeUInt16LE(2, 32); buf.writeUInt16LE(16, 34)
  buf.write('data', 36); buf.writeUInt32LE(n * 2, 40)
  for (let i = 0; i < n; i++) buf.writeInt16LE(Math.round(Math.sin((2 * Math.PI * hz * i) / rate) * 12000), 44 + i * 2)
  fs.writeFileSync(file, buf)
}

// Runs in the page: what the UI's capture code would get from getDisplayMedia.
const PROBE_SCRIPT = `(async () => {
  const out = {}
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: true })
  out.video = stream.getVideoTracks().map((t) => ({ label: t.label, ...t.getSettings() }))
  out.audio = stream.getAudioTracks().map((t) => ({ label: t.label, ...t.getSettings() }))
  out.webmOpus = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')

  // A frame: real pixels, or black (no Screen Recording permission)?
  const v = document.createElement('video')
  v.muted = true; v.srcObject = stream; await v.play()
  await new Promise((r) => setTimeout(r, 500))
  const c = document.createElement('canvas'); c.width = v.videoWidth; c.height = v.videoHeight
  const g = c.getContext('2d'); g.drawImage(v, 0, 0)
  const px = g.getImageData(0, 0, c.width, c.height).data
  let sum = 0, distinct = new Set()
  for (let i = 0; i < px.length; i += 4 * 997) { sum += px[i] + px[i + 1] + px[i + 2]; distinct.add(px[i] << 16 | px[i + 1] << 8 | px[i + 2]) }
  out.frame = { width: c.width, height: c.height, meanLevel: Math.round(sum / (px.length / (4 * 997)) / 3), distinctColours: distinct.size }

  // System audio: level while the tone plays, and whether MediaRecorder makes WebM chunks of it.
  if (stream.getAudioTracks().length) {
    const ctx = new AudioContext()
    const an = ctx.createAnalyser(); an.fftSize = 2048
    ctx.createMediaStreamSource(new MediaStream(stream.getAudioTracks())).connect(an)
    const rec = new MediaRecorder(new MediaStream(stream.getAudioTracks()), { mimeType: 'audio/webm;codecs=opus' })
    const chunks = []; rec.ondataavailable = (e) => e.data.size && chunks.push(e.data.size); rec.start(1000)
    const levels = []; const buf = new Float32Array(an.fftSize)
    for (let i = 0; i < 40; i++) {
      await new Promise((r) => setTimeout(r, 100))
      an.getFloatTimeDomainData(buf)
      levels.push(Math.sqrt(buf.reduce((s, x) => s + x * x, 0) / buf.length))
    }
    rec.stop(); await new Promise((r) => (rec.onstop = r))
    out.audioRms = { max: +Math.max(...levels).toFixed(4), median: +levels.sort((a, b) => a - b)[20].toFixed(4) }
    out.webmChunks = chunks
  }
  stream.getTracks().forEach((t) => t.stop())
  return out
})()`

async function probe() {
  for (const t of ['screen', 'microphone']) log(`[probe] ${t} permission: ${systemPreferences.getMediaAccessStatus(t)}`)
  const win = new BrowserWindow({ width: 400, height: 300, show: false })
  const page = path.join(os.tmpdir(), 'iris-probe.html') // file:// is a secure context, data: isn't
  fs.writeFileSync(page, '<title>probe</title>')
  await win.loadFile(page)
  const tone = path.join(os.tmpdir(), 'iris-probe-tone.wav')
  writeTone(tone)
  // after the stream opens; IRIS_PROBE_SILENT=1 is the control run
  if (!process.env.IRIS_PROBE_SILENT) setTimeout(() => spawn('afplay', [tone], { stdio: 'ignore' }), 1500)
  try {
    const result = await win.webContents.executeJavaScript(PROBE_SCRIPT, true)
    log('PROBE_RESULT ' + JSON.stringify(result, null, 2))
  } catch (err) {
    log('PROBE_ERROR ' + err)
  }
  app.quit()
}

// ---- app -----------------------------------------------------------------------------------

async function openUi() {
  if (!(await isUp(API))) start('uv', ['run', 'iris', 'serve'], path.join(REPO, 'backend'), 'backend')
  if (!(await isUp(UI))) start('npm', ['run', 'dev', '--', '--strictPort'], path.join(REPO, 'frontend'), 'ui')
  await Promise.all([waitFor(API), waitFor(UI)])
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    title: 'Iris',
    // Recording happens with Iris in the background: don't throttle its 1 fps frame ticker.
    webPreferences: { backgroundThrottling: false },
  })
  // Links in the notes open in the browser; the window only ever shows the Iris UI.
  const openOutside = (url) => {
    if (/^https?:/.test(url)) shell.openExternal(url)
  }
  win.webContents.setWindowOpenHandler(({ url }) => {
    openOutside(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, url) => {
    if (isIris(url)) return
    event.preventDefault()
    openOutside(url)
  })
  await win.loadURL(UI)
}

app.whenReady().then(async () => {
  handleDisplayMedia()
  try {
    await (PROBE ? probe() : openUi())
  } catch (err) {
    log(`[desktop] ${err.stack ?? err}`)
    app.quit()
  }
})

app.on('window-all-closed', () => app.quit())
app.on('quit', () => children.forEach((c) => c.kill()))
