# Plan: the Iris desktop app (macOS, unsigned, local)

Written 4 November 2026, after the capture spike (PR #17) and a backend packaging spike. Branch:
`feat/desktop-app`. Read `CLAUDE.md`, `README.md` and `docs/architecture/overview.md` first.

## Goal

A double-clickable `Iris.app` for the user's own Mac: open it, enter the OpenAI key once, record
any screen or window with system audio, and get the notes. No terminal, no repo, no `uv`.

**Done when** (on top of the definition of done in `CLAUDE.md`):

- `make desktop` builds `Iris.app` and a `.dmg` from a clean checkout.
- Double-clicking `Iris.app` (after the one-time right-click → Open for an unsigned app) shows the
  key screen on first run, then the Iris UI.
- A live recording of a window with sound, made in the app, produces finished notes. Uploads work.
- Quitting the app stops the backend; killing the Electron process makes the backend exit too; a
  second launch focuses the running app instead of starting another backend.
- `curl http://127.0.0.1:<port>/api/sessions` without the token is refused (401).
- The PR reports the measured app size, first launch time and warm launch time.

**Out of scope** (later roadmap items): Windows, code signing and notarization (the user has no
Apple Developer account yet), "save notes to a folder", replacing OpenCV, auto-update.

## What the spikes showed

- **Capture (merged, `desktop/main.cjs`):** Electron 45 records the screen or a window from the
  macOS system picker with system audio (`audio: 'loopback'`), through the UI's own
  `getDisplayMedia` call, with no native helper. Frames only stay at 1 fps with Iris covered by
  another window because `backgroundThrottling: false` is set on the window. Launching through
  `open` makes macOS attribute Screen Recording to the app, not the terminal.
- **Backend packaging (not merged; steps below):** a standalone CPython (python-build-standalone,
  as `uv python install` downloads) with Iris installed into it runs from any folder, including a
  path with a space, with an empty environment: no repo, no Homebrew, no `uv`. PyAV, OpenCV and
  resvg all did real work; an upload ran ingest and detect; HTTPS to OpenAI worked (a fake key got
  OpenAI's 401).
  - Size: Python + Iris 345 MB on disk, 129 MB zipped. OpenCV is 120 MB of it, PyAV 46, numpy
    34, openai 27, pip 12 (removable). Electron is 254 MB, so the app will be about 600 MB on disk.
  - Startup: warm imports 0.7 s, server up in about 2.3 s. **First run of a fresh copy: about
    40 s**, almost all of it macOS checking each new (ad-hoc signed) native library on first
    load: OpenCV 21 s, PyAV 13 s, 205 native files. Precompiling bytecode did not change it. Expect
    this once per install; signing and notarization may reduce it, which can only be tested with a
    Developer ID.
  - PyAV bundles FFmpeg 9 and OpenCV bundles FFmpeg 7; both load `libavdevice`, so the backend
    logs an ObjC duplicate-class warning. Harmless (Iris never uses FFmpeg's capture devices).
    Leave it.
  - The `iris` console script has the build path baked into its shebang, so the app must start the
    backend as `python3 -I -m iris …`, not through `bin/iris`.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| How the window gets the UI and reaches the API | **Option C:** Electron serves the built UI from a custom `app://` scheme and forwards `/api/*` to the backend | The backend stays the engine (it never serves a client's files); the UI survives a backend crash and can say so; the token never enters the page. The frontend and the API stay unchanged |
| Local API security | A random **per-launch token**, passed to the backend in the environment and added by the handler to every forwarded request | The backend otherwise accepts anything on `127.0.0.1` with the user's key loaded. EventSource and `<img>` can't set headers, but every request goes through the handler, so the page needs no token or cookie |
| API key | First-run key screen; stored encrypted with Electron `safeStorage` (macOS Keychain) in the app's `userData`; passed to the backend as `OPENAI_API_KEY` at launch | Never written in plain text, never baked into the build |
| Data folder | `~/Library/Application Support/Iris` (`app.getPath('userData')`), passed as `IRIS_DATA_DIR`; the backend runs with that folder as its cwd | A `.env` there still works for overrides (e.g. `IRIS_TRANSCRIBE_LANGUAGE`) |
| Python bundle | python-build-standalone + `uv pip install` into it, bytecode precompiled at build time, `pip` removed | Worked in the spike with no import hooks to maintain. A signed bundle must not write `.pyc` into itself |
| Framework | Stay on Electron | The system WebView (e.g. Tauri) has weak screen and system-audio capture; capture is Iris's core |
| Signing | Unsigned (ad-hoc), local use only | No Developer ID yet |

## Architecture

```
Iris.app
├── Electron main process (desktop/)
│     • single-instance lock
│     • picks a free port, makes a token, reads the key from safeStorage
│     • starts the bundled backend: python3 -I -m iris serve --port <port>
│         env: OPENAI_API_KEY, IRIS_DATA_DIR, IRIS_API_TOKEN, IRIS_PARENT_PID; cwd = userData
│     • waits for /api/health, watches the process, shows an error page if it dies
│     • protocol.handle('app'):
│         app://iris/api/*  → http://127.0.0.1:<port>/api/* (+ X-Iris-Token), streamed both ways
│         app://iris/<file> → frontend/dist/<file>
│         anything else     → frontend/dist/index.html (React Router)
│     • display-media handler (system picker, loopback audio), Iris UI only
│
├── Window (renderer): loads app://iris/ — the existing React UI, unchanged
└── Resources/backend/python: standalone Python with Iris installed
```

Development stays as today: `npm start` in `desktop/` (Vite + `uv run iris serve` from the repo),
and `make dev` for the browser. Use `app.isPackaged` to choose.

## Work, in order

### 1. Prove the handler first

Before building the rest, run the current UI through the `app://` handler with the backend
started from the repo (`uv run iris serve --port <port>`) and `frontend/dist` from
`npm run build`. Check, and write down the results for the PR:

- The progress stream (`/api/sessions/{id}/events`, server-sent events) arrives live, event by
  event, not all at the end.
- A multi-MB upload (`POST /api/sessions`, multipart, via XHR with progress) and live chunks
  (`POST /api/live/{id}/audio`, `/frame`) go through, streamed, not buffered whole.
- Figures (`<img src="/api/sessions/{id}/files/...">`) load.
- `getDisplayMedia` works on `app://iris/` (the scheme must be registered privileged with
  `standard`, `secure`, `supportFetchAPI`, `stream` before `app.ready`).
- Reloading on a deep route (`app://iris/session/<id>`) shows that page.

If streaming through `net.fetch` proves unreliable, fall back to a tiny local HTTP server in the
main process doing the same job (serve `dist/`, proxy `/api`), still separate from the backend.
Stop and report if neither works.

### 2. Backend changes (small, with tests)

- `backend/src/iris/__main__.py` so `python -m iris …` runs the CLI.
- `IRIS_API_TOKEN`: when set, every `/api/*` request must carry `X-Iris-Token` equal to it
  (constant-time compare), else 401. When unset, nothing changes (`make dev`, tests, CLI). Keep
  `/api/health` behind it too unless the handler needs otherwise; document the choice.
- `IRIS_PARENT_PID`: when set, a background task exits the server (cleanly) once that process is
  gone, so a crashed or force-quit Electron never leaves a backend running with the key loaded.
- Add both to `Settings` with comments, to `backend/.env.example` (commented out, "set by the
  desktop app"), and test: no token configured → open; missing or wrong token → 401; right
  token → OK; the parent watcher stops the server when the parent pid disappears (use a short
  poll interval and a fake pid check in tests; no real processes needed).

### 3. Electron app (`desktop/`)

Split the main process into small modules so the logic is testable without Electron:

- `handler` logic as pure functions (route a request path to `api` / `file` / `index`, resolve a
  file path safely inside `dist/`), plus the Electron glue.
- Backend lifecycle: free port (`net.createServer().listen(0)`), spawn, wait for health, log to a
  file in `userData/logs`, stop on quit, error page with "Restart" if it exits.
- Key screen: a small standalone page in `desktop/` (not the React app) shown before the UI when
  no key is stored, with a preload exposing only `saveApiKey(key)` and `hasApiKey()` over IPC.
  The key is never sent back to any page. Offer a way to change the key later (menu item).
- Keep from the spike: `backgroundThrottling: false`, the display-media handler (system picker,
  `loopback`), the capture origin check, the navigation guards (`will-navigate`,
  `setWindowOpenHandler` → `shell.openExternal` for http(s) only). Update `isIris` for `app://iris`.
- `app.requestSingleInstanceLock()`; a second launch focuses the existing window.
- `session.setPermissionRequestHandler`: allow only `media` (and display capture) for `app://iris`.
- Explicit `webPreferences`: `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false`.
- Send a Content-Security-Policy with `index.html` from the handler (scripts from `'self'` only;
  check KaTeX fonts and inline styles still work).

### 4. Frontend change (small)

Turn off voice processing for shared audio: request
`audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }` from
`getDisplayMedia` in `frontend/src/capture/liveCapture.ts` (keep the existing fallback when audio
is refused). Leave the microphone's processing on. Update the capture tests.

### 5. Packaging

- `desktop/scripts/build-backend.sh` (spike steps, all inside a build folder, never `/tmp` paths
  baked in):
  1. `uv python install 3.12 --install-dir <build>/pythons`, copy the
     `cpython-3.12.*-macos-aarch64-none` folder to `<build>/python`.
  2. `uv export --no-dev --frozen --no-emit-project -o <build>/requirements.txt` (in `backend/`).
  3. `uv pip install --python <build>/python/bin/python3 --break-system-packages -r requirements.txt`,
     then `--no-deps backend/`.
  4. Remove `pip`; `python3 -I -m compileall -q -j 0 <build>/python/lib`.
  5. Smoke test from a copy in a path with a space, with `env -i HOME=$HOME PATH=/usr/bin:/bin`:
     import `av`, `cv2`, `numpy`, `resvg_py`, `iris.main`; decode a generated video with PyAV; diff
     and write a JPEG with OpenCV; rasterize an SVG with resvg. Fail the build if any step fails.
- `electron-builder` config in `desktop/`: `appId`, `productName: Iris`, arm64 `dmg` target,
  `extraResources` for the Python bundle and `frontend/dist`, `Info.plist` strings
  (`NSMicrophoneUsageDescription`; `NSAudioCaptureUsageDescription` for system audio). Unsigned:
  check what Apple Silicon needs for the bundle to launch (likely an ad-hoc signature, identity
  `-`), and that signing does not choke on the Python libraries.
- `make desktop`: build the frontend, build the backend bundle, run electron-builder.
- `.gitignore` the build outputs.

### 6. Measure and document

- Report: `.app` size, `.dmg` size, first launch (fresh install) to UI shown, warm launch.
- Update `docs/roadmap.md` (Desktop app items, with the numbers), `docs/architecture/overview.md`
  (system shape: the desktop app, `app://`, the token), `docs/development.md` (building and
  running the app, where its logs and data live), and `README.md` (quick start for the app).

## Security checklist for the handler and app

- Only Iris's own UI is ever loaded in Iris windows (navigation guards, window-open handler).
- File serving: decode the path, resolve it, refuse anything outside `dist/` (`..`, `%2e%2e`,
  absolute paths, symlinks out).
- The forward target is always `http://127.0.0.1:<port>`; only the path and query come from the
  request.
- The handler always overwrites `X-Iris-Token`; the page never sees the token.
- Do not set `corsEnabled` on the scheme unless needed; other origins must not read `app://`.
- Permissions: media and display capture only, for `app://iris` only.
- The key: stored only via `safeStorage`; only `hasApiKey`/`saveApiKey` exposed; never logged.
- Keep Electron current.

## Tests

- Backend (`pytest`): the token middleware and the parent watcher (step 2).
- Desktop (new): `node --test` for the pure handler logic: routing (`/api/...` → forward with the
  token, files, deep routes → `index.html`), path containment (traversal attempts refused), the
  forward URL can't be redirected. Add `cd desktop && npm test` to CI
  (`.github/workflows/ci.yml`) and to the checks in `CLAUDE.md`.
- Frontend: the capture constraints change (step 4).

## Working agreement

- Follow `CLAUDE.md`: checks before review, commit style `<type>(<domain>): <comment>` with no AI
  attribution or co-author lines, no PR footer.
- **Paid API calls only with the user's go-ahead.** Use a fake key for everything that doesn't
  need real transcription. The user's real key is in `backend/.env` of the main checkout
  (`/Users/jasonho/Desktop/Iris-notetaker`); a recording costs roughly $0.05–0.10 per minute.
- **The user must do the GUI steps:** granting Screen Recording and microphone to `Iris.app` in
  System Settings, picking a window in the system picker, and the first right-click → Open. Ask
  them when you get there, with exact instructions.
- Before the PR: run the `code-reviewer` agent on the branch diff against `main`, fix or get
  waivers for blockers and should-fixes, have it re-verify, then open the PR with the summary,
  test results, measurements and review outcome. The user merges.
