# Iris
[![CI](https://github.com/Jho0413/Iris-notetaker/actions/workflows/ci.yml/badge.svg)](https://github.com/Jho0413/Iris-notetaker/actions/workflows/ci.yml)

**Notes that see what was shown.**

Iris turns a session where someone shows things while talking (a meeting, a lecture, a demo) into
Markdown notes that include what was on screen. The slide, the whiteboard sketch or the diagram
being discussed is rebuilt as a clean visual and placed next to what was said about it.

Upload a recording or record live from the browser or the macOS app. Iris transcribes the audio, finds what changed
on screen, reads it (math, text, tables, code, figures), redraws each figure and checks it against
the original, and writes step-by-step notes in a style suited to the session. Everything runs
locally; model calls go to OpenAI.

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and Node 22+.

```sh
make setup                              # install backend, frontend and desktop app deps
cp backend/.env.example backend/.env    # add OPENAI_API_KEY
make dev                                # API on :8000, UI on http://localhost:5173
```

### The macOS app

```sh
make setup && make desktop              # desktop/dist/Iris-<version>-arm64.dmg (Apple Silicon)
```

Open the `.dmg`, drag Iris to Applications and open it. The first launch asks for your OpenAI API
key (stored encrypted with the macOS Keychain), then shows Iris. **Record live** records any
screen or window with its sound; macOS asks for Screen Recording and microphone access the first
time. No terminal, `uv` or repo is needed to run it.

More commands, the `iris` CLI and troubleshooting: [`docs/development.md`](docs/development.md).

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request and on pushes to `main`, and
must pass before merging:

- **Backend:** `ruff check`, `ruff format --check` and `pytest`
- **Frontend:** `npm run lint`, `typecheck`, `test` and `build`
- **Desktop:** `npm test` (the app's main-process logic, in plain Node)

Run the same checks locally with `make lint` and `make test`.

## Project structure

```
backend/src/iris/
  api/            FastAPI routes: sessions, live input, progress events, usage, health
  sources/        upload + browser live: turn input into audio.wav + frames/ in a session
  pipeline/       stages (transcribe, detect, align, select, write, render, assemble,
                  synthesize) and PLAN, the stage order
  live/           processing a live session while it records
  jobs/           runs a session's PLAN and publishes progress
  store/          SQLite session index + session folder layout (layout.py lists every file)
  llm/            OpenAI client, usage and cost recording, error handling
  cli.py          the `iris` command
  config.py       settings (IRIS_* environment variables)
  user_settings.py  what the Settings page saved (settings.json), under IRIS_* and .env
  models.py       shared types: API models and every stage's output
frontend/src/
  api/            typed client (generated from OpenAPI) + progress events
  capture/        browser mic + screen-share capture
  components/     notes view (Markdown, KaTeX, figures) and progress
  notes/          rehype transform for step cards, callouts and figures
  session/        session state, progress stream, upload and cost hooks
  pages/          library, upload, live, session, settings
  settings/       the Settings page's language list and wording
desktop/          the macOS app (Electron): app:// handler, backend lifecycle, key screen,
                  packaging (scripts/, electron-builder.yml)
docs/
  roadmap.md      what is done, what is next
  development.md  commands, cost tracking, troubleshooting
  architecture/   overview, pipeline, live sessions, errors and retries, the desktop app
```
