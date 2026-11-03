# Iris architecture

Status: **draft**. Section 1 written; section 2 covers the stages built so far; sections 3–6 to come,
one at a time.

1. [Overview and the life of a session](#1-overview-and-the-life-of-a-session)
2. [Pipeline stages](#2-pipeline-stages) *(draft)*
3. Data model and local storage
4. API and live updates
5. Models, cost and latency budget
6. Test set and how we judge quality

---

## 1. Overview and the life of a session

### What Iris does

Iris takes a session where someone shows things while talking (a meeting, a lecture, a demo) and
writes notes that include **what was on screen**, placed next to what was said about it. Audio-only
notetakers lose the slide, the whiteboard sketch or the diagram the conversation was about. Iris keeps
it.

### Principles

1. **Professional visuals, accurate to the session.** What was shown on screen is rebuilt as a clean
   visual (typeset maths, plotted graphs, rendered diagrams, illustrations, cleaned screenshots), with
   no restriction on visual type. Redraws must match what was shown and said: each is verified against
   the source frames and transcript, and source frames stay on disk so any visual can be regenerated.
2. **One pipeline, many sources.** An uploaded file, live browser capture, and later a desktop app or
   a meeting bot all produce the same thing: timestamped audio and frames. Everything downstream is
   unaware of where they came from.
3. **Local work first, model calls last.** Finding what changed on screen is free OpenCV work on the
   machine. Models only see the few moments that survive it.
4. **Never lose a session.** Every stage writes its output to disk as it goes. A crash, a closed tab or
   a bad prompt means re-running a stage, not re-recording.
5. **Small and measurable.** Each stage reads files and writes files, so it can be run and tested on its
   own, and quality is judged against a fixed set of real recordings (section 6).

### System shape

```
┌─ Web UI (TypeScript) ──────────────┐          ┌─ Backend (Python, FastAPI) ───────────────────┐
│  upload · live capture · notes     │── HTTP ─▶│  API ──▶ job runner ──▶ pipeline stages        │
│  view · library · export           │◀─ events─│                              │                 │
└────────────────────────────────────┘          │                   sessions/<id>/  + SQLite     │
                                                └──────────────────────────────┬────────────────┘
                                                                               ▼
                                                                         OpenAI API
```

Everything runs on your laptop. The UI and the backend are two local processes. Later, a desktop
app (Tauri or Electron) wraps both, with the backend running as a bundled sidecar.

### Components

| Component | Job | Notes |
|---|---|---|
| **Sources** | Turn an input into timestamped audio and frames on disk | v1: file upload, browser live capture. Later: desktop capture, meeting bot |
| **Pipeline** | Transcribe, find visual moments, read screens, rebuild figures, write notes | Stages detailed in section 2 |
| **Job runner** | Runs a session's stages, in parallel where possible | In-process `asyncio`; single user, so no queue server |
| **Store** | Session files plus an index of sessions | Files under `sessions/<id>/`, metadata in SQLite |
| **API** | Create sessions, accept live input, stream progress, serve notes | FastAPI; typed TS client generated from its OpenAPI schema |
| **UI** | Upload, live view, notes view, library, export | Designed in `docs/frontend.md` |

### Key libraries

| Library | Used for |
|---|---|
| **FastAPI** + **Pydantic** | API and typed request/response models |
| **PyAV** | Decoding uploads and live chunks: audio resampled to 16 kHz mono, and video frames read faster than real time. Its wheels bundle FFmpeg, so there is no system install |
| **OpenCV** (headless) + **numpy** | Frame diffs, "has the screen settled", finding changed regions, cropping. Plain frame diffs cover cut detection, so PySceneDetect is not needed |
| **RapidOCR** *(optional, planned)* | Reading text on slides to match visuals to what was said. Declared as the `ocr` extra; not wired into alignment yet |
| **openai** | Transcription, screen reading, notes, figure rendering (SVG and images) and figure checks |
| **resvg-py** | Rasterizing sanitized model-written SVG, for figure checks and PNG export. Bundled, no system install |
| **markdown-it-py** + **mdformat** | Parsing and normalizing notes Markdown without breaking LaTeX |
| **faster-whisper** *(later)* | Local transcription, if cost or privacy calls for it |
| **SQLite** | Session index and status |
| **uv** | Python environment and dependencies |
| **openapi-typescript** | Generating the UI's typed API client |
| **react-markdown** + **remark-math** + **KaTeX** | Rendering notes in the UI, with typeset math |

### Life of a session: upload

1. **Create.** The UI uploads a file. The backend saves it as `sessions/<id>/source.<ext>` and starts a
   job.
2. **Ingest** (seconds, not real time). PyAV extracts 16 kHz mono audio and samples frames at a
   low fixed rate (`IRIS_FRAME_FPS`, default 1 fps), downscaled, each tagged with its timestamp.
3. **Transcribe and detect, in parallel.**
   - *Transcribe:* audio is split at silences into chunks, transcribed concurrently, and merged into
     one timestamped transcript.
   - *Detect moments:* frames are diffed locally to find where the screen changed and where it
     settled. Each moment is a stretch of time with one representative "settled" frame, plus its
     *builds* (content added over time, such as lines of working), following canvas scrolling.
4. **Align.** Each moment gets the transcript spoken while it was on screen.
5. **Select (read the screen).** A vision model looks at batches of moments with their builds and
   words, keeps the ones worth noting, and reads them into math (LaTeX), text, tables, code and
   figures. Each figure gets a caption, a self-contained description, a source crop and a renderer
   (`svg`, `image` or `screenshot`).
6. **Write and render, in parallel.**
   - *Write:* a model identifies the session type (meeting, lecture, demo) and writes the notes in a
     matching style, in chronological sections, placing `[visual:<id>]` markers where each figure
     belongs. Worked examples and processes are written step by step.
   - *Render:* each figure is rebuilt as SVG or a generated image (or kept as a screenshot) and
     checked by a vision model against its source crop and description. Failures are retried with
     feedback, then the other renderer is tried; if nothing passes, the source crop is used and
     marked unverified.
7. **Assemble.** Markers are resolved to the rendered figures. The notes are stored, shown, and
   exportable as Markdown with figures embedded.

The UI receives progress events throughout (server-sent events): transcript chunks as they finish,
moments as they are found, figures as they are rendered, and the notes as they are written.

### Life of a session: live

The same stages run incrementally, while the session is happening.

1. **Capture.** The browser records the mic (and tab audio if shared) and the screen share. It sends
   short audio chunks every few seconds, and screen frames at a low fixed rate. Because the backend is
   local, bandwidth is not a concern, so change detection stays in one place (Python) rather than
   being duplicated in the browser.
2. **Store.** The backend appends audio and frames to the session as they arrive. That is the
   recording, so a live session can be fully re-processed afterwards like an upload.
3. **Process as it goes.** The transcript extends every chunk. A moment closes when the screen moves
   on, which triggers selection for it. The notes are kept as a running draft, updated section by
   section.
4. **End.** The last moment closes, the final notes pass runs over the whole session, and the result
   replaces the draft.

### Sources are pluggable

A source has one job: write timestamped audio and frames into a session. The pipeline only reads what
is in the session, so adding a source never touches the pipeline.

| Source | When | How |
|---|---|---|
| File upload | v1 | PyAV over the file |
| Browser live | v1 | `getUserMedia` / `getDisplayMedia`, chunks posted to the backend |
| Desktop capture | Later | Native screen and system-audio capture in the desktop app |
| Meeting bot | Later | A bot joins Zoom, Meet or Teams and streams the call (e.g. via Recall.ai) |

### Not in v1

- Accounts, hosting, sharing links
- Desktop app and meeting bot (designed for, not built)
- Editing notes inside Iris

### Open questions for later sections

- Frame sampling rate for uploads: 1 fps or 2 fps? Currently 1 fps. *(section 2)*
- Long sessions: answered for now. Notes are written per section and joined; a final synthesis pass
  is planned (`docs/roadmap.md`). *(section 2)*
- Do you want speaker labels ("Alice: …") in meeting notes? *(section 5)*
- Which OpenAI models give timestamps? Only `whisper-1` returns timestamps, so it is the default;
  whether it fits the budget is part of cost tracking (`docs/roadmap.md`). *(section 5)*
- Progress uses server-sent events and live input uses HTTP POSTs per chunk. Revisit if live needs
  WebSockets. *(section 4)*

---

## 2. Pipeline stages

*Draft: the stages with notable behaviour. Ingest, transcribe, align and assemble are described in
their module docstrings for now.*

### Detect

Each moment records its *builds* (content added over time, such as lines of handwritten working) and
follows canvas scrolling, so a worked example or a sketch drawn on a scrolling page stays one
moment. Details are in the `detect.py` docstring.

### Select (read the screen)

- Batches of final frames, with their builds and aligned transcript, go to `IRIS_VISION_MODEL`. It
  reads everything on screen: math as LaTeX, text, tables, code, and figures, each figure with a
  self-contained brief and a renderer (`svg`, `image` or `screenshot`).
- Batch size: `IRIS_SELECTION_BATCH_SIZE` (default 6, range 1–20). A summary of content already
  captured is passed to later batches, so nothing is listed twice.
- Bounded context: transcript up to 24,000 characters per batch, split across its moments; OCR up
  to 1,000 characters per moment (OCR isn't wired in yet). Long context is marked as excerpts from
  the start, middle and end. The full transcript stays on disk for note writing.
- Completed batches are cached in `selection_batches/` and reused after failures and on re-runs.
  The output and a progress event are saved after each batch.

### Write

- `IRIS_NOTES_MODEL` writes meeting, lecture or demo notes from what was said and what was on screen.
- Worked examples are written step by step: problem, steps with typeset working and reasoning,
  answer, then key-idea and watch-out callouts. All math is LaTeX (`$...$`, `$$...$$`). Each figure
  is placed once, beside the explanation it illustrates.
- All transcript text is used. Long transcripts are split into chronological sections of up to
  `IRIS_NOTES_CONTEXT_CHARS` evidence characters per call (default 24,000, range 2,000–60,000).
  Oversized speech segments are split without dropping text or inventing timestamps.
- Sections keep the first section's title and style and are joined in order. There is no final
  synthesis pass yet.
- Validated sections are cached in `notes_sections/`; damaged entries are regenerated. The draft is
  saved, and a progress event emitted, after each section. Final notes are available after
  assembly. Showing the partial draft in the UI is future work.

### Render

Runs alongside note writing and rebuilds every figure professionally.

- `IRIS_DIAGRAM_MODEL` draws exact figures (graphs, charts, schematics) as SVG; `IRIS_IMAGE_MODEL`
  draws realistic or illustrative figures from the brief. Figures that are already clean, or too
  detailed to redraw reliably (architecture diagrams, detailed maps, dense charts), stay as sharp
  screenshots.
- `IRIS_VISION_MODEL` checks each result against the source crop and brief. Failures are retried
  with the problems as feedback (`IRIS_RENDER_ATTEMPTS` per renderer), then the other renderer is
  tried. If nothing passes, the source crop is used and recorded in `renders.json` as unverified,
  so the notes never show a redraw that failed its check.
- Up to `IRIS_RENDER_CONCURRENCY` figures render at once. Model-written SVG is sanitized before it
  is stored. Completed renders are cached in `render_cache/`.
- Exports embed SVG figures as PNG, since Markdown viewers reject SVG data URIs.
