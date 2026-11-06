# The desktop app

`Iris.app` (macOS, Apple Silicon) is the web UI and the backend with no terminal, repo or `uv`:
Electron shows the UI and runs a bundled backend as its child process. Building and running it,
and where it keeps its data, are in [`../development.md`](../development.md) ("Desktop app").

```
Iris.app
├── Electron main process (desktop/main.cjs, desktop/src/)
│     • one instance at a time; a second launch focuses the running window
│     • picks a free port, makes a random token, reads the key (decrypted once per launch)
│     • refuses to start with --remote-debugging-port/-pipe (packaged app)
│     • starts the backend: python3 -I -u -B -m iris serve --host 127.0.0.1 --port <port>
│         --secrets-from-stdin
│         stdin: {"openai_api_key": <key or null>, "api_token": <token>}, one line, then closed
│         env: IRIS_DATA_DIR, IRIS_PARENT_PID; cwd = userData
│     • waits for /api/health (with the token), watches the process, offers Restart if it dies
│     • app:// protocol handler:
│         app://iris/api/*  → http://127.0.0.1:<port>/api/* (+ X-Iris-Token), streamed both ways
│         app://iris/<file> → frontend/dist/<file>
│         anything else     → index.html (client-side routes)
│     • display-media handler (macOS system picker, system audio as "loopback")
│     • menu bar icon (Tray): Record Screen… and Stop, relayed to the Live page
├── Window (renderer): app://iris/, the web UI unchanged
└── Contents/Resources/backend/python (standalone Python with Iris), Resources/frontend (built UI)
```

## Decisions

| Decision | Choice | Why |
|---|---|---|
| How the window gets the UI and reaches the API | Electron serves the built UI from a custom `app://` scheme and forwards `/api/*` to the backend | The backend stays the engine and never serves a client's files; the UI still loads, and says the engine isn't running, if the backend stops; the token never enters the page. The frontend and the API are the same as in the browser |
| Local API security | A random per-launch token, given to the backend on its stdin and added by the handler to every forwarded request | The backend otherwise accepts anything on `127.0.0.1` with the user's key loaded. `EventSource` and `<img>` can't set headers, but every request goes through the handler, so the page needs no token or cookie |
| API key | Entered in the key window (on a new install's first launch only, a two-step welcome, Welcome then Connect, with Set up later; otherwise from Settings or the menu), checked once with OpenAI before it is saved; stored encrypted with Electron `safeStorage` (its key is in the macOS Keychain) in `userData`; passed to the backend on its stdin at each start, never in its environment. Without one Iris still opens and warns that it can't make notes | Never written in plain text, never built in, never sent to a page, never readable with `ps`; a wrong key is caught when it is entered, not by the first session |
| Data folder | `~/Library/Application Support/Iris` (`userData`), as `IRIS_DATA_DIR` and the backend's working directory | The Settings page saves `settings.json` there; a `.env` there still sets `IRIS_*` options and wins over the page. The app keeps its own flags (`welcomeShown`) in `app-state.json` there |
| Python bundle | python-build-standalone with Iris installed into it, bytecode precompiled at build time | Runs from any folder with an empty environment, with no import hooks to maintain |
| Framework | Electron | The system WebView (e.g. Tauri) has weak screen and system-audio capture, and capture is Iris's core |
| Signing | Ad-hoc (`identity: "-"`), not notarized | No Developer ID yet: the app is for the user's own Mac |
| Secrets to the backend | One JSON line on the backend's stdin (`iris serve --secrets-from-stdin`, `backend/src/iris/launch_secrets.py`), then the pipe is closed | Any process of the same user reads another's environment with `ps eww <pid>`, with no prompt (macOS hides it only for Apple's own binaries, not the bundled Python). A pipe is readable only by the backend |
| Electron fuses | `runAsNode`, `enableNodeOptionsEnvironmentVariable` and `enableNodeCliInspectArguments` off; `onlyLoadAppFromAsar` and `enableEmbeddedAsarIntegrityValidation` on (`electron-builder.yml`) | Otherwise anything running as the user could start Iris with `ELECTRON_RUN_AS_NODE=1`, `NODE_OPTIONS=--require …`, `--inspect` or a changed `app.asar` and run its own code as Iris, which the Keychain trusts with the key without asking |

## The `app://` handler

`protocol.handle('app')` (`src/handler.cjs`) routes each request with pure functions
(`src/routes.cjs`):

- **API requests** go to `http://127.0.0.1:<port>` with only the path and query from the request,
  so a crafted path (`//evil.example`, `@host`) can't change the target. The handler always
  overwrites `X-Iris-Token`, drops hop-by-hop headers, and rewrites a backend redirect to the same
  path on `app://iris` (or drops it). While the backend is down it answers 503 itself; if the
  backend doesn't answer, 502.
- **Files** are served from `frontend/dist` after decoding the path, resolving it and checking the
  real path (symlinks included) is still inside `dist/`. A missing path without a file extension is
  a client-side route (`index.html`); a missing file is a 404.
- **`index.html`** is sent with a Content-Security-Policy: scripts from the app only, plus the
  inline theme script by its sha256 (computed from `index.html` at startup, so editing that script
  needs no change here). Inline styles are allowed (React and KaTeX set style attributes).
- **API responses** carry `default-src 'none'; …; sandbox`. Fetch, XHR and `EventSource` ignore
  it; it applies only when a response is shown as a document (a figure opened full size in its own
  window), which then runs no script and has an opaque origin.
- The scheme is registered as `standard`, `secure`, `supportFetchAPI`, `stream` and `codeCache`,
  but not `corsEnabled`: other origins can't read `app://` responses.

**The forward uses `node:http`, not Electron's `net.fetch`** (`src/forward.cjs`). Piping honours
backpressure. Measured with a 516 MB upload through the Upload page: the main process grew by
89 MB with `node:http`, 574 MB with Node's `fetch`, and 1.6 GB with `net.fetch`, which reads the
whole request body ahead of the socket. Server-sent events stream event by event, and closing an
`EventSource` closes the backend connection, so the backend sees the client go away.

## How `app://` pages differ from the browser

- **`navigator.sendBeacon` throws** ("Beacons are only supported over HTTP(S)"). The live page
  ends a session on `pagehide` with a beacon, so it falls back to a `keepalive` fetch, which works
  on `app://` (`sendBeaconOrKeepalive` in `frontend/src/capture/liveCapture.ts`).
- **XHR upload progress never fires** through a custom protocol, so the upload page shows
  "Uploading…" until the response. Locally, 500 MB takes about a second.
- **Electron cancels a `beforeunload` close silently**, without a dialog. Two pages ask before
  unloading: the live page while it records, and Settings with an unsaved change. So the main
  process shows its own dialog (`will-prevent-unload`), worded by page in `src/unload.cjs`: "Stop
  recording?" for the live page, "Leave without saving?" (or "Quit without saving?", which cancels
  the quit if kept) otherwise.
- **Capture errors** name Iris and the System Settings pane instead of the browser
  (`describeCaptureError`, when the page's protocol is `app:`).

## Backend lifecycle

`src/backend.cjs` (plain Node, tested with a fake backend script):

- **Start:** a free port, a fresh token, the process spawned with a clean environment, then
  `/api/health` polled with the token, for up to 180 s (a first launch can be slow, see Packaging).
  One start runs at a time: a second `start()` joins the first, so there is never a second backend.
- **Stop:** SIGTERM (uvicorn finishes requests and runs shutdown), then SIGKILL after 10 s. `stop()`
  also cancels a start under way, which then rejects with `stopped: true`.
- **Crash:** an exit the app didn't ask for, after the backend was healthy, shows one dialog
  (Restart, Show Logs, Quit). A start that fails reports through its rejection instead, so there is
  one message, not two. The UI stays loaded; its uploads retry until the restarted backend takes
  them.
- **The app dies:** the backend watches `IRIS_PARENT_PID` (`backend/src/iris/parent.py`) and shuts
  itself down once that process is gone. Measured: 0.3 s after the app was killed with SIGKILL.
- **Quit:** if a session is recording or processing, Iris asks first (what was captured is kept,
  and next time the session page offers Finish for a recording, Try again for notes being made).
  Then it stops the backend and exits. Closing the window doesn't quit: Iris keeps running, and
  making notes, in the Dock and the menu bar. While a recording runs, closing the window only
  hides it (see "Menu bar").

## Security

- **Only Iris's UI is ever loaded in Iris windows.** In-page navigation is allowed within
  `app://iris` only; anything else is cancelled and http(s) links open in the browser. `window.open`
  is always denied, except a session file ("Open full size" on a figure), which opens in an Iris
  window with no preload. `<webview>` is refused.
- **Windows** use `contextIsolation`, `sandbox`, no `nodeIntegration`, and
  `backgroundThrottling: false` (so frames keep coming at 1 fps while Iris is behind other windows).
- **Permissions:** only `media` (microphone) and `display-capture`, only for `app://iris`; every
  other request and check is refused (`src/policy.cjs`).
- **The key screen** (`pages/key.html`) has a preload exposing only `save`, `hasApiKey` and
  `cancel`. The main process checks that each call comes from that page in the key window. The key
  is never sent back to a page and never logged. Saving first checks the new key with one free
  request to OpenAI (listing the models, `src/keycheck.cjs`): rejected (401/403), unreachable, or
  OpenAI unable to check, each says so in the window and nothing is saved.
- **The main window's bridge** (`pages/preload-main.cjs`) gives the Iris UI three actions, plus
  the menu bar's recording state and commands (see "Menu bar"), and nothing to read:
  `changeKey()` opens the key window, then restarts the engine with the new key,
  `removeKey()` asks once, deletes the key and restarts the engine without it (both refuse, with
  OK only, while a session is recording or making notes: the restart would cut it short;
  `src/busy.cjs` words it), and `savePdf(title)` asks where to save, then prints the page (its print
  stylesheet) with `printToPDF` and writes the file there (`src/pdf.cjs` names it after the
  session). The main process accepts them only from the top frame of the main window showing
  `app://iris` (`policy.fromIrisWindow`). Whether a key is set reaches the UI from the engine
  (`/api/settings` `api_key_set`), never from the app.
- **The API token** is compared in constant time and guards every request, including
  `/api/health` and the OpenAPI docs (`backend/src/iris/api/token.py`); without one (`make dev`,
  the CLI, tests) nothing changes.
- **Secrets reach the backend on its stdin**, never its environment: `ps eww <backend pid>` shows
  no key or token. At each start (launch, Restart Engine, a key change or removal) the app writes
  one line, `{"openai_api_key": <key or null>, "api_token": <token>}`, and closes the pipe;
  `iris serve --secrets-from-stdin` reads it before the server starts (16 KB at most, unknown
  fields refused), passes the values to the server's `Settings` and never puts them in
  `os.environ`. A missing or malformed line stops the backend with a message in `backend.log`
  that never includes the line. The key from the app wins over a `.env` in `userData`; with no
  key (null) a `.env` may still supply one. A backend that exits before reading its stdin can't
  crash the app (the write's EPIPE is logged, by code only).
- **No code runs as Iris but Iris's own.** The packaged app's Electron fuses turn off
  `ELECTRON_RUN_AS_NODE`, `NODE_OPTIONS` and `--inspect`/`--inspect-brk` (and `SIGUSR1`), and load
  only `app.asar`, checked against the hash electron-builder writes into `Info.plist`. No fuse
  covers Chromium's remote debugging, so the packaged app quits at once, before any window or
  engine, when started with `--remote-debugging-port` or `--remote-debugging-pipe`
  (`src/debugging.cjs`, logged in `main.log`). Development (`npm start`) keeps every debugging
  switch.

### Limits

These keep the key from other processes that only run as the user. They don't stop:

- **A keylogger or screen reader** catching the key as it is typed into the key window.
- **Reading the session files** in `userData` (recordings, transcripts, notes): they are plain
  files, readable by anything running as the user.
- **Anything with admin rights**, such as a debugger attached with `sudo`, or a process that can
  read another's memory.
- **A process that asks the Keychain itself**: macOS prompts for "Iris Safe Storage", and a user
  who clicks Always Allow lets it through.

## Capture

The UI's own `getDisplayMedia` call is answered by the macOS system picker (`useSystemPicker`,
macOS 15+), where the user picks any screen or window, with system audio as `loopback`; no native
helper is needed. Shared audio is requested with echo cancellation, noise suppression and gain
control off, since it is a recording of what plays, not a voice in a room; the microphone keeps
them. `Info.plist` carries `NSMicrophoneUsageDescription` and `NSAudioCaptureUsageDescription`.

## Menu bar

An Iris icon in the macOS menu bar (Electron `Tray`, `src/tray.cjs` for what it shows) starts and
stops a recording from any app, with the window closed too:

- **Idle:** Record Screen…, Open Iris, Quit Iris. **Recording:** the icon turns red with the
  elapsed time beside it, and the menu offers Stop and Make Notes. While starting, finishing the
  upload, or stopped with notes not started ("Recording stopped", the Live page's Try again),
  it offers only Show Iris and Quit.
- **Recording still happens in the Live page**, whose capture code runs in the window. The main
  process only relays (`relay` in `src/tray.cjs`, a pure reducer): Record Screen… shows the window
  (making it if it was closed) and, once the UI has loaded and subscribed
  (`iris-app:record-listening`), sends it `start`; the UI opens `/live`, which starts as if its
  button was pressed. Stop sends `stop`, which runs the Live page's own Stop, so the notes are made
  as usual. A stop is never queued for a page that loads later; a start waiting for the UI is
  dropped if the window closes or the engine fails to start first.
- **The page reports its phase** (`iris-app:recording`, `{phase, startedAt}`), checked once by
  `parseReport`; a reload, a closed window or a crashed renderer resets the icon to idle. A stop
  that fails (the Live page's "Recording stopped", with Try again) shows the window, which may
  have been hidden.
- **`getDisplayMedia` needs no user gesture here:** Electron passes a request without one to its
  display-media handler (and the system picker). Measured with Electron 45.
- **Closing the window while recording hides it** instead (`hideOnClose`), since closing would end
  the recording; the menu bar or the Dock shows it again. A fullscreen window leaves fullscreen
  first, so no empty Space is left behind. Sharing the Iris window itself is the exception: macOS
  stops capturing a window once it is hidden, so the recording finishes, as for Stop sharing. Quit still asks first ("Quit Iris?"),
  then closes it for real. Reloading still asks "Stop recording?".
- **The icons** (`assets/`) are the Iris mark (`pages/iris.svg`) at 18 pt, rasterized with resvg:
  black as a template image (macOS tints it for the menu bar) and red while recording.

## Packaging

`make desktop` runs `desktop/scripts/build-app.sh`: the UI, then the backend bundle
(`build-backend.sh`), the icon (from `build-resources/icon.svg`, rasterized with resvg and
`iconutil`), then electron-builder (`electron-builder.yml`).

- **The backend bundle:** `uv python install` (python-build-standalone) copied into a build
  folder, the locked dependencies (`uv export --frozen`) and Iris installed into it, pip,
  setuptools, Tk and the console scripts removed, bytecode precompiled. The backend runs as
  `python3 -I -u -B -m iris`: isolated from `PYTHON*` variables and the user site, unbuffered so the
  log is live, never writing `.pyc` into the signed app, and through `-m` because the console
  scripts' shebangs hold the build machine's path.
- **The smoke test** (`smoke_backend.py`) runs a copy from a path with a space under `env -i`:
  PyAV encodes and decodes a video, OpenCV diffs frames and writes a JPEG, resvg rasterizes an SVG,
  and the real server starts and refuses requests without the token. The build fails if any step
  does.
- **Size:** `Iris.app` 581 MB (Python and Iris 327 MB, of which OpenCV is the largest part;
  Electron 243 MB), the `.dmg` 224 MB.
- **Startup:** a warm launch shows the UI in about 2.6 s (the backend is healthy in about 2.2 s).
  The first launch of a copy never run on that Mac took about 40 s in testing, almost all of it
  macOS checking each of about 200 native libraries once; signing and notarization may shorten it.
- **Known and harmless:** PyAV and OpenCV each bundle FFmpeg and both load `libavdevice`, so the
  backend logs an Objective-C duplicate-class warning. Iris never uses FFmpeg's capture devices.
- **Ad-hoc signing** means every rebuild is a new signature, so macOS asks again for Screen
  Recording and for the Keychain item ("Iris Safe Storage").
