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
  is used. Source frames stay on disk for verification and regeneration, not in the notes

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
make test                         # backend tests + frontend typecheck
make gen-api                      # regenerate the UI's typed client after API changes
```

Running the pipeline without the UI:

```sh
cd backend
uv run iris process path/to/recording.mp4   # new session, full pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # run one stage on its own
```

### Layout

```
backend/src/iris/
  api/            FastAPI routes: sessions, live input, SSE progress events, health
  sources/        upload + browser live: turn input into audio.wav + frames/ in a session
  pipeline/
    stages/       transcribe, detect, align, select, write, render, assemble (one file in, one out)
    __init__.py   stage registry and PLAN (stage order; groups run concurrently)
    live.py       incremental processing while a live session runs
  jobs/runner.py  runs a session's PLAN as an asyncio task, publishes progress
  store/          SQLite session index + on-disk session folder layout
  llm/            OpenAI client
  models.py       shared types: API models and every stage's output format
frontend/src/
  api/            typed client (generated from OpenAPI) + SSE hook
  capture/        browser mic + screen-share capture
  pages/          library, upload, live, session (notes view)
```

Detection records each moment's *builds* (content added over time, such as lines of handwritten
working) and follows canvas scrolling, so a worked example or a sketch drawn on a scrolling page stays
one moment. Selection sends batches of up to six final frames, with their builds and aligned
transcript, to `IRIS_VISION_MODEL`, which reads everything on screen: math as LaTeX, text, tables,
code, and figures with a self-contained brief and a renderer (`svg`, `image` or `screenshot`). A
summary of already captured content is passed to later batches so nothing is listed twice. Set
`IRIS_SELECTION_BATCH_SIZE` (1–20) to adjust the batch size. Completed decisions are cached in each session's
`selection_batches/` directory; unchanged batches are reused after failures and on reruns.
Transcript context is limited to 24,000 characters per batch, divided across its moments.
Long context is explicitly marked as excerpts sampled from the start, middle, and end; the full
transcript remains on disk for note writing. OCR context is limited to 1,000 characters per moment.
Selection outputs and progress events are saved after each batch.

Note writing uses `IRIS_NOTES_MODEL` to produce meeting, lecture or demo notes from what was said
and what was on screen. Worked examples are written step by step (problem, steps with typeset
working and the reasoning, answer, key-idea and watch-out callouts), all math is LaTeX (`$...$`,
`$$...$$`), and each figure is placed once, beside the explanation it illustrates. All transcript
text is processed. Longer transcripts are
split into chronological sections, with up to `IRIS_NOTES_CONTEXT_CHARS` serialized evidence
characters per call (default 24,000; range 2,000–60,000, plus prompt and small metadata). Oversized
speech segments are split without dropping text or inventing timestamps. Sections retain the first
section's title and style and are joined in order, without an additional global synthesis pass.
Validated sections are cached in `notes_sections/`; damaged entries are regenerated. The draft is
saved and a progress event emitted after each section. Final notes become available after assembly;
partial draft display in the UI and live processing remain future work.

Rendering runs alongside note writing and rebuilds every figure professionally: `IRIS_DIAGRAM_MODEL`
draws exact figures (graphs, charts, schematics) as sanitized SVG, and `IRIS_IMAGE_MODEL` draws
realistic or illustrative figures from the brief. Figures that are already clean, or too detailed to
redraw reliably (architecture diagrams, detailed maps, dense charts), are kept as sharp screenshots. `IRIS_VISION_MODEL` checks each result against the source and
the brief; failures are retried with the problems as feedback (`IRIS_RENDER_ATTEMPTS` per renderer),
then the other renderer is tried, and only if nothing passes is the source crop used (recorded in
`renders.json` as unverified). Source crops stay on disk, never in the notes. Exports embed SVG
figures as PNG, since Markdown viewers reject SVG data URIs.

Session data lives in `backend/data/` (`iris.db` plus `sessions/<id>/`).

## Docs

- [`docs/roadmap.md`](docs/roadmap.md): milestones, remaining work and completion criteria
- [`docs/architecture.md`](docs/architecture.md): architecture and pipeline design
- `docs/frontend.md`: screens and UI design (to come)
