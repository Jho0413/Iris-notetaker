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
built UI, not Vite. Test desktop behaviour in `make app`; a `make desktop` build takes minutes
longer, since Apple notarizes it ([Signing the app](#signing-the-app)).

## The CLI

Run in `backend/`:

```sh
uv run iris process path/to/recording.mp4   # a new session, the whole pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # one stage on its own
uv run iris show <session_id>               # status and, for a failed run, the raw error
uv run iris cost [<session_id>]             # model calls and cost (default: the newest session)
uv run iris replay-live <file>...           # play recordings into a running engine's live API
uv run iris mcp                             # the read-only MCP server, as an assistant starts it
```

`mcp` speaks MCP on stdin and stdout, so it is for an assistant to start, not for typing at;
connecting one is in [`assistants.md`](assistants.md#connecting).

`iris serve --secrets-from-stdin` is how the desktop app starts the engine: it reads the key and
the token from one line on its input. It can't be combined with `--reload`, because a reload
starts the engine again in a new process, which has no such line to read.

`replay-live` needs a running engine and makes real, paid model calls through it, so point that
engine at a scratch data folder (`IRIS_DATA_DIR`). It plays a file the way the browser records:

1. The audio is re-encoded as one WebM/Opus stream with short clusters, as the browser's recorder
   writes it, and sent in pieces of about five seconds. Only the first piece carries the stream
   header, as with the browser's own pieces. The stream is written through a small buffer, so
   it is known closely how far into it each moment of audio reaches, and pieces are cut there.
2. Once a second it sends the latest picture the video has shown by then, scaled as the browser
   scales a screen, as one screen share from start to end. A screen recording often stores a
   picture only when the screen changes, so a picture is sent again until the next one shows. A
   file with no real video (an audio file, even one with cover art) is replayed as an audio-only
   recording, with no frames.
3. Everything is paced in real time, or faster with `--speed` (the session's clock stays the
   file's), then it asks for notes.
4. It follows the session's progress and reports when the first transcript, moment, figure and
   notes section arrived, how many sections were written before notes were asked for, how long
   making notes took, what live work was kept, the total cost and what making notes added.

To use them on the desktop app's data, set the folder:
`IRIS_DATA_DIR="$HOME/Library/Application Support/Iris" uv run iris show <id>`. In the app, the
session ids are the folder names in `sessions/` there.

## Checks

The checks every change must pass, and what to regenerate after an API change, are in
[`CLAUDE.md`](../CLAUDE.md) ("Checks"); CI runs the same ones. For a quick local run, `make lint`
and `make test` cover most of them (not the frontend build).

## Releasing

Releasing is on hold until the workflow can sign the app: it has no signing certificate yet, so
`make desktop` stops there before building anything.

1. Bump the version in a PR, and merge it: in `desktop/`, `npm version <x.y.z> --no-git-tag-version`
   (it updates `package.json` and its lockfile together). The app, the `.dmg`'s name and the
   release all read it from `package.json`.
2. Run the **Release** workflow from `main`: Actions → Release → Run workflow, or
   `gh workflow run release.yml`. It refuses a version that is already released, drafted or
   tagged, then builds the app on a GitHub-hosted Apple Silicon Mac with `make desktop`, including
   its smoke tests and signature checks. The tests in "Checks" aren't rerun: CI ran them before the
   merge.
3. It leaves a **draft** release, "Iris <version>", with the `.dmg` and notes listing the merged
   PRs. Check it, then publish it; publishing creates the `v<version>` tag on the commit it was
   built from. To build the same version again, delete its draft first.

*Why a draft:* nothing reaches anyone until you have looked at the build. *Why by hand:* a release
is a decision, not something every merge should make, and while the repository is private its Mac
minutes count against the free Actions allowance at about ten times the Linux rate (free once it
is public).

## Settings

Settings are `IRIS_*` environment variables, plus the key as `OPENAI_API_KEY`; the full list is in
`backend/src/iris/config.py` and most are in `backend/.env.example`. The Settings page saves what a
user can change to `settings.json` in the data folder; an environment variable or `.env` line wins
over it, and the page shows that setting as locked. A damaged `settings.json` never stops Iris
starting: an unreadable file is ignored whole and an invalid value on its own, each with a warning
in the log, and the next save from the page replaces the file. `.env` and `data/` are read from the
current folder, so run the engine and the CLI from `backend/`.

## Debugging a failed session

The UI shows only a short message of ours. To see what went wrong:

- `uv run iris show <session_id>`: the stage it stopped at, whether the recording itself was the
  problem (`input`) or anything else (`general`), and the raw error.
- The engine's log: the terminal running `make dev`, or `logs/backend.log` for the app
  (**Iris → Show Logs**). It has the traceback, each retry and each figure that fell back.
- `uv run iris cost <session_id>`: totals per stage, model and purpose, with a count of failed
  calls. Each call, with its error, is in the session's `usage_calls.jsonl`.

## Signing the app

`make desktop` signs the app with a **Developer ID Application** certificate and has Apple
notarize it, so a downloaded copy opens on any Mac with only macOS's usual first-open question. It
needs, once per Mac:

1. The certificate with its private key in the login keychain (`security find-identity -v -p
   codesigning` lists it). Make it at developer.apple.com → Certificates, from a certificate
   request made in Keychain Access on the same Mac, or import the `.p12` backup of one. Only the
   account holder of an Apple Developer Program membership can make one.
2. A notarytool keychain profile named `iris-notary` (or another name in `APPLE_KEYCHAIN_PROFILE`):
   `xcrun notarytool store-credentials iris-notary --apple-id <Apple ID> --team-id <team ID>`, with
   an app-specific password from account.apple.com.

Without either, `make desktop` stops before it builds anything and says which is missing. If Apple
rejects a build, `xcrun notarytool log <submission id> --keychain-profile iris-notary` lists what
it objected to.

## Permissions and the Keychain

If you refused the microphone or Screen Recording, allow Iris in System Settings → Privacy &
Security → Microphone (or Screen & System Audio Recording), then quit and reopen it. When macOS
asks to let Iris use "Iris Safe Storage" in your keychain, choose **Always Allow**, or it asks at
every launch.
