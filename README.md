# Iris
[![CI](https://github.com/Jho0413/Iris-notetaker/actions/workflows/ci.yml/badge.svg)](https://github.com/Jho0413/Iris-notetaker/actions/workflows/ci.yml)

**Notes that see what was shown.**

Iris turns a session where someone shows things while talking (a meeting, a lecture, a demo) into
notes that include what was on screen. The slide, the whiteboard sketch or the diagram being
discussed is placed in the notes next to what was said about it.

## How it works

1. **Capture** a session: upload a recording, or record live from the browser (mic plus screen share).
2. **Transcribe** the audio with timestamps.
3. **Detect moments** where the screen changed and then settled, each with a representative frame.
4. **Align** each moment with what was said while it was on screen.
5. **Select** the moments worth keeping and rebuild each as a professional visual, checked against
   what was shown and said.
6. **Write** notes in a style suited to the session (meeting, lecture or demo), with each visual placed
   where it belongs.
7. **Show and export** the finished notes, with visuals embedded.

## Requirements

### Users and environment
- Single user, running locally on a laptop
- No accounts or hosting

### Input
- **v1:** upload a recording first, then live capture in the browser (mic plus screen share)
- **Later:** desktop app capture and a meeting bot (Zoom, Meet, Teams)
- Sources are pluggable: each produces timestamped audio and frames, and the pipeline does not depend
  on where they came from

### Visuals
- Professional, not raw: anything shown on screen (handwriting, sketches, diagrams, slides) is rebuilt
  as a clean visual in the notes: typeset maths, plotted graphs, rendered diagrams, tables,
  illustrations, or a cleaned-up screenshot where the screen is already clean
- No restriction on visual type; each visual uses whichever renderer fits it best
- Accurate to the session: every redraw is checked against the source frames and transcript before it
  is used. If no redraw passes, the original crop is shown instead. Source frames stay on disk for
  verification and regeneration

### Notes
- Worked examples are written step by step: what was written, why, with the visual for that step
- Style adapts to the content: meeting, lecture or demo
- Shown in the app, and downloadable with visuals embedded

### Performance and cost
- Typical sessions: 30–90 minutes
- A 1-hour upload produces notes within a few minutes, with partial results streaming in as they
  are ready
- Live sessions update the notes as the session runs
- Processing cost: $0.50–2 per hour of session

### Stack
- **Backend:** Python, FastAPI, PyAV (bundles FFmpeg), OpenCV, SQLite
- **Frontend:** TypeScript web UI
- **Models:** OpenAI
- **Later:** a desktop app (Tauri or Electron) wrapping the UI, with the backend as a sidecar

## Development

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 22+. Nothing else to install system-wide:
PyAV ships with FFmpeg built in.

```sh
make setup                        # install backend + frontend deps
cp backend/.env.example backend/.env   # add OPENAI_API_KEY
make dev                          # API on :8000, UI on http://localhost:5173
make test                         # backend tests, frontend tests and typecheck
make lint                         # ruff + frontend lint (CI also runs build)
make gen-api                      # regenerate the UI's typed client after API changes
```

Running the pipeline without the UI:

```sh
cd backend
uv run iris process path/to/recording.mp4   # new session, full pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # run one stage on its own
uv run iris cost <session_id>               # model calls and cost per stage, model and purpose
```

Every model call is recorded in the session's `usage.json` (also `GET /api/sessions/{id}/usage`):
tokens, images, audio seconds, latency and cost from the price table in `config.py`. Calls to
models missing from the table are reported as unpriced, not guessed. Cached work costs nothing.

### Layout

```
backend/src/iris/
  api/            FastAPI routes: sessions, live input, SSE progress events, health
  sources/        upload + browser live: turn input into audio.wav + frames/ in a session
  pipeline/
    stages/       transcribe, detect, align, select, write, render, assemble (read session files,
                  write their own)
    __init__.py   stage registry and PLAN (stage order; groups run concurrently)
    live.py       incremental processing while a live session runs (not implemented yet)
  jobs/runner.py  runs a session's PLAN as an asyncio task, publishes progress
  store/          SQLite session index + on-disk session folder layout (layout.py lists every file)
  llm/            OpenAI client; usage.py records every model call and its cost (usage.json)
  config.py       Settings (IRIS_* environment variables, see backend/.env.example)
  cli.py          the `iris` command
  events.py       progress event bus
  svg.py          sanitizing and rasterizing model-written SVG
  markdown.py     math-safe markdown helpers
  export.py       Markdown export with embedded figures
  models.py       shared types: API models and every stage's output format
frontend/src/
  api/            typed client (generated from OpenAPI) + SSE hook
  capture/        browser mic + screen-share capture
  components/     notes view (Markdown, KaTeX, figures) and stage progress
  notes/          rehype transform for step cards, callouts and figures
  pages/          library, upload, live, session (notes view)
```

How each stage works, its settings and its caches: [`docs/architecture.md`](docs/architecture.md),
section 2.

Session data lives in `backend/data/` (`iris.db` plus `sessions/<id>/`).

## Docs

- [`docs/roadmap.md`](docs/roadmap.md): milestones, remaining work and completion criteria
- [`docs/architecture.md`](docs/architecture.md): architecture and pipeline design
- [`docs/frontend.md`](docs/frontend.md): screens, states, progress events and visual design of the UI
