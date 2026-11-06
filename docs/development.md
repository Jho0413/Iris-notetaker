# Development

Setup, everyday commands, the desktop app, cost tracking and troubleshooting. How we work
(conventions, commits, definition of done) is in [`../CLAUDE.md`](../CLAUDE.md).

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 22+. Nothing else to install system-wide:
PyAV ships with FFmpeg built in.

```sh
make setup                        # install backend, frontend and desktop app deps
cp backend/.env.example backend/.env   # add OPENAI_API_KEY
make dev                          # API on :8000, UI on http://localhost:5173
```

Settings are `IRIS_*` environment variables (`backend/src/iris/config.py`, documented in
`backend/.env.example`). Session data lives in `backend/data/` (`iris.db` plus `sessions/<id>/`).

The **Settings** page (gear icon in the top bar) saves the options a user can change, so far the
spoken language, to `settings.json` in the data folder (`backend/data/` here, the app's data
folder in the desktop app). A saved change applies to the next session without restarting the
engine (and to **Try again** when it starts from transcription; one that resumes later keeps the
transcript it has) (`iris/user_settings.py`, `GET`/`PUT /api/settings`). A value set
explicitly still wins: an `IRIS_*` environment variable or `.env` line pins it, and the page shows
it locked with the variable's name. So `make dev`, the CLI and tests behave as before; the CLI
(`iris process`, `iris run`) also reads `settings.json`. A damaged `settings.json` is ignored with a
warning in the log, and the next save replaces it.

## Commands

```sh
make test                         # backend, frontend and desktop tests, frontend typecheck
make lint                         # ruff + frontend lint (CI also runs build)
make gen-api                      # regenerate the UI's typed client after API changes
make usage [<session_id>]         # model usage and cost (most recent session by default)
```

Running the pipeline without the UI:

```sh
cd backend
uv run iris process path/to/recording.mp4   # new session, full pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # run one stage on its own
uv run iris cost [<session_id>]             # model calls and cost per stage, model and purpose
uv run iris show <session_id>               # status, and for a failed run what it failed on
uv run iris replay-live <file>              # play a recording into the live API (architecture/live.md)
```

## Desktop app

`desktop/` is the macOS app (Electron): the same UI, served from `app://iris`, and the backend as
its child process. How it works: [`architecture/desktop.md`](architecture/desktop.md).

```sh
make app                          # try a change in the app: rebuilds the UI, then runs it with the
                                  # backend from the repo (no packaging; shows as "Electron" in the Dock)
make desktop                      # desktop/dist/mac-arm64/Iris.app and Iris-<version>-arm64.dmg
cd desktop && npm start           # the same as make app, without rebuilding frontend/dist
cd desktop && npm test            # handler, key store, backend lifecycle and policy tests (plain Node)
```

`make desktop` builds the UI, then a standalone Python with Iris installed
(`desktop/scripts/build-backend.sh`: python-build-standalone via `uv python install`, the locked
dependencies via `uv export`, bytecode precompiled, a smoke test from a path with a space and an
empty environment), then the app with electron-builder. Apple Silicon only; it takes about four
minutes from a clean checkout. The app is ad-hoc signed, not notarized: it is for your own Mac.
A copy downloaded from elsewhere needs right-click → **Open** the first time.

**Where things live** (packaged app):

- Data: `~/Library/Application Support/Iris` (`iris.db`, `sessions/<id>/`), the backend's
  `IRIS_DATA_DIR` and working directory. `settings.json` there holds what the Settings page
  saved; a `.env` there still sets `IRIS_*` options (e.g. `IRIS_TRANSCRIBE_LANGUAGE=fr`) and wins
  over the page.
- The OpenAI key: `openai-key.bin` in the same folder, encrypted with Electron `safeStorage` (the
  key to it is in the macOS Keychain as "Iris Safe Storage"). Entered on the welcome shown on a new
  install's first launch, once and never again (two steps, Welcome then Connect; **Set up later**
  opens Iris without one; `welcomeShown` in `app-state.json` in the same folder records it) or from **Settings → OpenAI** (Add key…, Replace…, Remove)
  or **Iris → Change OpenAI API Key…**; a new key is checked with OpenAI before it is saved, and
  the engine restarts to use it. While a session is recording or making notes, the key can't be
  changed or removed (Iris says so). Without a key, the Library and Settings warn that Iris can't make
  notes. In development (`npm start`) the engine falls back to `backend/.env`'s key when none is
  stored, so removing the stored key doesn't make it keyless there.
- Logs: `logs/main.log` (the app) and `logs/backend.log` (the engine, the same output as
  `iris serve`) in that folder. **Iris → Show Logs** opens it. The key is never logged.
- `iris show` and `iris cost` work on the app's data from a checkout:
  `IRIS_DATA_DIR="$HOME/Library/Application Support/Iris" uv run iris show <id>` in `backend/`.

Development (`npm start`) uses `~/Library/Application Support/Iris Dev` for its logs and key, and
the repo's `backend/.env` and `backend/data/` as `make dev` does (a key saved through the menu
overrides `.env`). The window loads the built UI, not Vite: for UI work use `make dev` in a browser.

Closing the window keeps Iris running (and making notes) in the Dock. Quitting stops the engine,
and asks first if a session is still recording or processing; what was captured is kept, and the
session page offers **Resume** or **Finish and make notes** next time.

## Cost tracking

Every model call is recorded in the session's `usage.json` (also `GET /api/sessions/{id}/usage`):
tokens, images, audio seconds, latency and cost from the price table in `config.py`. Calls to
models missing from the table are reported as unpriced, not guessed. Cached work costs nothing.
The UI shows each session's cost in the library and the session rail, per step in its Details.

## Troubleshooting

When a run fails, the UI shows only a short message of our own ("Iris couldn't finish making the
notes. Try again; finished steps are kept.", or what to fix about the recording) and the step it
stopped at; it never shows the error itself. To see what went wrong:

- **`uv run iris show <session_id>`** (in `backend/`) prints the session's title, source, status,
  the stage it stopped at, the failure kind, the message the user saw, and the raw error with its
  time. The session id is in the page's URL (`/sessions/<id>`).
- **The backend log** (the terminal running `make dev` or `iris serve`) has the full traceback,
  every retry of a temporary API error, and each figure that fell back to its source crop.
- **`uv run iris cost <session_id>`** counts failed model calls; each one's error is in the
  session's `usage.json`.

Temporary API errors (timeouts, rate limits, server errors) are retried per unit of work after the
SDK's own retries, waiting `IRIS_API_RETRY_DELAYS_S` (default `[15, 60, 120]` seconds). A missing
or rejected `OPENAI_API_KEY`, exhausted credit or a missing model fails the run at once; fix it and
**Try again**. The whole policy is in [`architecture/errors.md`](architecture/errors.md).

### Desktop app

- **"Iris's engine didn't start" or "stopped unexpectedly":** the dialog offers Restart and Show
  Logs; `logs/backend.log` has the reason. The engine runs only while the app does, on a random
  local port, and answers only requests carrying the app's per-launch token (a `curl` to it gets
  401).
- **Screen Recording or the microphone:** macOS asks the first time you record. If you said no,
  allow Iris in System Settings → Privacy & Security → Screen & System Audio Recording (or
  Microphone), then quit and reopen Iris. Each rebuild is a new ad-hoc signature, so after
  `make desktop` macOS may ask again, and asks to let Iris use "Iris Safe Storage" in your
  keychain: choose **Always Allow**, or it asks at every launch.
- **Starting is slow the first time:** macOS checks each native library of a new install once
  (about 40 s was measured for a copy never run on that Mac); later launches take about 3 s.
