# The desktop app

`Iris.app` (macOS, Apple Silicon) is the web UI and the backend with no terminal, repo or `uv`:
Electron shows the UI and runs a bundled backend as its child process. Building and running it,
and where it keeps its data, are in [`../development.md`](../development.md) ("Desktop app").

```
Iris.app
├── Electron main process (desktop/main.cjs, desktop/src/)
│     • one instance at a time; a second launch focuses the running window
│     • picks a free port, makes a random token, reads the key (decrypted once per launch)
│     • starts the backend: python3 -I -u -B -m iris serve --host 127.0.0.1 --port <port>
│         env: OPENAI_API_KEY, IRIS_DATA_DIR, IRIS_API_TOKEN, IRIS_PARENT_PID; cwd = userData
│     • waits for /api/health (with the token), watches the process, offers Restart if it dies
│     • app:// protocol handler:
│         app://iris/api/*  → http://127.0.0.1:<port>/api/* (+ X-Iris-Token), streamed both ways
│         app://iris/<file> → frontend/dist/<file>
│         anything else     → index.html (client-side routes)
│     • display-media handler (macOS system picker, system audio as "loopback")
├── Window (renderer): app://iris/, the web UI unchanged
└── Contents/Resources/backend/python (standalone Python with Iris), Resources/frontend (built UI)
```

## Decisions

| Decision | Choice | Why |
|---|---|---|
| How the window gets the UI and reaches the API | Electron serves the built UI from a custom `app://` scheme and forwards `/api/*` to the backend | The backend stays the engine and never serves a client's files; the UI still loads, and says the engine isn't running, if the backend stops; the token never enters the page. The frontend and the API are the same as in the browser |
| Local API security | A random per-launch token, given to the backend in its environment and added by the handler to every forwarded request | The backend otherwise accepts anything on `127.0.0.1` with the user's key loaded. `EventSource` and `<img>` can't set headers, but every request goes through the handler, so the page needs no token or cookie |
| API key | A first-run key screen; stored encrypted with Electron `safeStorage` (its key is in the macOS Keychain) in `userData`; passed to the backend as `OPENAI_API_KEY` at launch | Never written in plain text, never built in, never sent to a page |
| Data folder | `~/Library/Application Support/Iris` (`userData`), as `IRIS_DATA_DIR` and the backend's working directory | The Settings page saves `settings.json` there; a `.env` there still sets `IRIS_*` options and wins over the page |
| Python bundle | python-build-standalone with Iris installed into it, bytecode precompiled at build time | Runs from any folder with an empty environment, with no import hooks to maintain |
| Framework | Electron | The system WebView (e.g. Tauri) has weak screen and system-audio capture, and capture is Iris's core |
| Signing | Ad-hoc (`identity: "-"`), not notarized | No Developer ID yet: the app is for the user's own Mac |

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
  and the session page offers Resume or Finish next time). Then it stops the backend and exits.
  Closing the window doesn't quit: Iris keeps running, and making notes, in the Dock.

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
  is never sent back to a page and never logged.
- **The API token** is compared in constant time and guards every request, including
  `/api/health` and the OpenAPI docs (`backend/src/iris/api/token.py`); without
  `IRIS_API_TOKEN` (`make dev`, the CLI, tests) nothing changes.

## Capture

The UI's own `getDisplayMedia` call is answered by the macOS system picker (`useSystemPicker`,
macOS 15+), where the user picks any screen or window, with system audio as `loopback`; no native
helper is needed. Shared audio is requested with echo cancellation, noise suppression and gain
control off, since it is a recording of what plays, not a voice in a room; the microphone keeps
them. `Info.plist` carries `NSMicrophoneUsageDescription` and `NSAudioCaptureUsageDescription`.

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
