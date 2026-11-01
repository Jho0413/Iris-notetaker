# Iris

**Notes that see what was shown.**

Iris turns a session where someone shows things while talking (a meeting, a lecture, a demo) into
notes that include what was on screen. The slide, the whiteboard sketch or the diagram being
discussed is placed in the notes next to what was said about it.

## How it works

1. **Capture** a session: upload a recording, or record live from the browser (mic plus screen share).
2. **Transcribe** the audio with timestamps.
3. **Detect moments** where the screen changed and then settled, each with a representative frame.
4. **Align** each moment with what was said while it was on screen.
5. **Select** the moments worth keeping, with the best frame or crop and a short caption.
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
- Faithful only: every image in the notes is a real frame from the session, or a crop of one
- No redrawn, annotated or generated images in v1

### Notes
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
    stages/       transcribe, detect, align, select, write, assemble (one file in, one file out)
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

Visual selection sends batches of up to six representative frames with their aligned transcript to
`IRIS_VISION_MODEL`. Set `IRIS_SELECTION_BATCH_SIZE` (1–20) to adjust this. Kept visuals are full
frames or validated crops of real frames. Completed decisions are cached in each session's
`selection_batches/` directory; unchanged batches are reused after failures and on reruns.
Transcript context is limited to 24,000 characters per batch, divided across its moments.
Long context is explicitly marked as excerpts sampled from the start, middle, and end; the full
transcript remains on disk for note writing. OCR context is limited to 1,000 characters per moment.
Selection outputs and progress events are saved after each batch.

Note writing uses `IRIS_NOTES_MODEL` to produce meeting, lecture or demo notes, placing selected
visuals beside the relevant explanation. All transcript text is processed. Longer transcripts are
split into chronological sections, with up to `IRIS_NOTES_CONTEXT_CHARS` serialized evidence
characters per call (default 24,000; range 2,000–60,000, plus prompt and small metadata). Oversized
speech segments are split without dropping text or inventing timestamps. Sections retain the first
section's title and style and are joined in order, without an additional global synthesis pass.
Validated sections are cached in `notes_sections/`; damaged entries are regenerated. The draft is
saved and a progress event emitted after each section. Final notes become available after assembly;
partial draft display in the UI and live processing remain future work.

Session data lives in `backend/data/` (`iris.db` plus `sessions/<id>/`).

## Docs

- [`docs/architecture.md`](docs/architecture.md): architecture and pipeline design
- `docs/frontend.md`: screens and UI design (to come)
