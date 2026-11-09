# Development

How to set Iris up, run it, check a change and debug a session. How it works is in
[`overview.md`](overview.md); how we work (conventions, commits, definition of done) is in
[`../CLAUDE.md`](../CLAUDE.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) and Node 22.12 or later. Nothing else system-wide: PyAV ships
  with FFmpeg built in.
- The desktop app needs macOS on Apple Silicon.

## Setup

```sh
make setup                              # backend, frontend and desktop dependencies
cp backend/.env.example backend/.env    # then add OPENAI_API_KEY
```

## Running

```sh
make dev       # the engine on 127.0.0.1:8000 and the UI on http://localhost:5173
make app       # the desktop app from the code: rebuilds the UI, no packaging
make desktop   # the packaged app and its .dmg, in desktop/dist/
```

`make app` shows as "Electron" in the Dock. `make desktop` builds
`desktop/dist/mac-arm64/Iris.app` and `desktop/dist/Iris-<version>-arm64.dmg`.

Where each keeps its data and logs:

| | Data (`iris.db`, `sessions/<id>/`) | Key and logs |
|---|---|---|
| `make dev` | `backend/data/` | `backend/.env`; logs in the terminal |
| `make app` | `backend/data/` (shared with `make dev`) | `~/Library/Application Support/Iris Dev` |
| Packaged app | `~/Library/Application Support/Iris` | the same folder (`logs/`) |

Because `make app` shares `backend/data/` with `make dev`, don't record in a browser while the dev
app runs: the app stops recordings it finds left open there
([`recording-live.md`](recording-live.md)). For UI work use `make dev`: the dev app loads the
built UI, not Vite. Test desktop behaviour in `make app`; each `make desktop` build has a new
signature, so macOS asks for its permissions and the Keychain again.

## The CLI

Run in `backend/`:

```sh
uv run iris process path/to/recording.mp4   # a new session, the whole pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # one stage on its own
uv run iris show <session_id>               # status and, for a failed run, the raw error
uv run iris cost [<session_id>]             # model calls and cost (default: the newest session)
uv run iris replay-live <file>...           # play recordings into a running engine's live API
```

`replay-live` needs a running engine and makes real, paid model calls through it, so point that
engine at a scratch data folder (`IRIS_DATA_DIR`).

To use them on the desktop app's data, set the folder:
`IRIS_DATA_DIR="$HOME/Library/Application Support/Iris" uv run iris show <id>`. In the app, the
session ids are the folder names in `sessions/` there.

## Checks

The checks every change must pass, and what to regenerate after an API change, are in
[`CLAUDE.md`](../CLAUDE.md) ("Checks"); CI runs the same ones. For a quick local run, `make lint`
and `make test` cover most of them (not the frontend build).

## Settings

Settings are `IRIS_*` environment variables, plus the key as `OPENAI_API_KEY`; the full list is in
`backend/src/iris/config.py` and most are in `backend/.env.example`. The Settings page saves what
a user can change to `settings.json` in the data folder; an environment variable or `.env` line
wins over it, and the page shows that setting as locked. `.env` and `data/` are read from the
current folder, so run the engine and the CLI from `backend/`.

## Debugging a failed session

The UI shows only a short message of ours. To see what went wrong:

- `uv run iris show <session_id>`: the stage it stopped at, whether the recording itself was the
  problem (`input`) or anything else (`general`), and the raw error.
- The engine's log: the terminal running `make dev`, or `logs/backend.log` for the app
  (**Iris → Show Logs**). It has the traceback, each retry and each figure that fell back.
- `uv run iris cost <session_id>`: totals per stage, model and purpose, with a count of failed
  calls. Each call, with its error, is in the session's `usage_calls.jsonl`.

## Opening a downloaded copy of the app

The app isn't notarized, so macOS blocks a copy downloaded from elsewhere the first time. Open it
once, then go to System Settings → Privacy & Security and click **Open Anyway** next to the Iris
message (right-click → Open no longer works on macOS 15 and later). A copy you built yourself
opens directly.

## Permissions and the Keychain

If you refused the microphone or Screen Recording, allow Iris in System Settings → Privacy &
Security → Microphone (or Screen & System Audio Recording), then quit and reopen it. When macOS
asks to let Iris use "Iris Safe Storage" in your keychain, choose **Always Allow**, or it asks at
every launch.
