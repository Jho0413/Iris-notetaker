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

The same work as an upload, done while the session is happening. What is done live *is* the final
work: at stop, only the last few seconds are new, so finished notes arrive about a minute after stop
however long the session was. Live processing is best effort: the captured chunks stay the
recording, and if anything live fails the session is processed like an upload.

```
Browser                    API routes                 LiveProcessor (one per recording, asyncio tasks)
MediaRecorder 5 s chunks ─▶ POST /live/{id}/audio ─wake─▶ audio lane:  reorder ▸ decode ▸ cut at pauses ▸ transcribe ─┐
Frame ticker 1 fps ───────▶ POST /live/{id}/frame ─wake─▶ screen lane: hold back ▸ normalise ▸ MomentDetector ─────────┤
                                                                                                                       ▼
                                                          live timeline: transcript, moments, selection, watermarks
                                                                                                                       │
                                                          select lane: gate ▸ align ▸ select ▸ render (background) ◀──┤
                                                          draft lane:  horizon ▸ frozen sections + tail ▸ notes_draft ◀┘
Stop ─────────────────────▶ POST /live/{id}/end ───────▶ job: finish lanes ▸ ingest ▸ promote live outputs ▸ PLAN from write
Live + Session pages ◀──── SSE events, GET /sessions/{id}/notes/draft
```

**Capture and store** (unchanged from the live-capture work). The browser posts 5 s WebM chunks
(`seq` 0, 1, 2, ...) and ~1 fps JPEG frames (`t` = seconds since start, the audio's clock). Each is
stored atomically under `live/` and never modified; the route then wakes the lane that consumes it
and returns without waiting for any processing.

**Lanes.** `iris/live/` holds one processor per recording session (`LiveRegistry`, owned by the
job runner, started at `POST /live`, or on the first chunk after a backend restart). It runs four
lanes as asyncio tasks. Lanes communicate only through the **live timeline**
(`iris/live/timeline.py`), which holds what each lane has finished and up to when:

| Watermark | Meaning | Advanced by |
|---|---|---|
| `transcribed_to` (T_a) | the transcript is complete up to here | audio lane |
| `closed_to` (T_m) | every moment that ends by here is closed | screen lane |
| `selected_to` (T_s) | every closed moment that ends by here has been read | select lane |
| `horizon` | `min(T_a, T_s)`: evidence before it is final | (derived) |

Lanes wait on watermarks (an `asyncio.Condition`), never poll. A lane that has finished (after
stop) or failed counts as "through" any time, so nothing downstream waits forever.

| Lane | Input | Does | Publishes |
|---|---|---|---|
| **Audio** | `live/audio/*.webm` | Decodes the contiguous run of chunks into one PCM stream (`live/audio.pcm`). Cuts windows at the quietest point 10–30 s after the last cut (never at a chunk edge) and transcribes them, at most `IRIS_LIVE_TRANSCRIBE_CONCURRENCY` at once, each prompted with the previous window's text. Commits windows in order | transcript segments, T_a, `TRANSCRIPT_CHUNK` |
| **Screen** | `live/frames/*.jpg` | Holds frames back `IRIS_LIVE_FRAME_HOLDBACK_S` (session time) to order them, samples them onto the `frame_fps` grid and normalises them with ingest's own helpers (size fixed by the first readable frame), writing `frames/` with ingest's names and bytes, and feeds `MomentDetector` the frames read back from `frames/`, as detect reads them | closed moments, T_m, `MOMENT_FOUND` |
| **Select** | closed moments + transcript | Gates each moment until T_a ≥ its end + `IRIS_LIVE_SPEECH_PAD_S`, aligns it, and reads it with the select stage's batch step and one call in flight (moments that pass the gate meanwhile go together in the next batch, up to `selection_batch_size`; visual ids continue across batches). Renders each figure in the background (`IRIS_LIVE_RENDER_CONCURRENCY`); a failed render is left to the render stage; the draft shows the source crop until it passes | contents, visuals, T_s, `VISUAL_SELECTED`, `VISUAL_RENDERED` |
| **Draft** | timeline | Packs evidence before the horizon into sections like write does. A section is *frozen* once the horizon passes a moment boundary beyond its budget, and written once (cached in `notes_sections/`); the open *tail* is rewritten at most every `IRIS_LIVE_DRAFT_REFRESH_S` | `notes_draft.json`, `NOTES_UPDATED` |

**Joining what was split.** Inputs arrive in pieces, so each lane has a rule for putting them back
together:

- *Words split across chunks:* raw chunks are never transcribed (only chunk 0 has the WebM header,
  and edges fall mid-word). Windows are cut at pauses in the decoded stream, and the previous
  window's text is passed as the prompt.
- *Late, repeated or lost chunks:* the decoder only consumes the contiguous run of `seq`. A missing
  chunk is waited for `IRIS_LIVE_AUDIO_GAP_WAIT_S` (longer than the browser's upload timeout plus a
  retry), then decoding resumes at the next WebM cluster and the gap is silence, as in ingest.
  Frames are ordered by holding them back briefly; a frame later than that is skipped live, and
  recorded as skipped input only if ingest would have sampled it (its grid cell has no earlier
  frame).
- *Content built up over time:* `MomentDetector` keeps a slide build or a worked example as one
  moment with ordered builds, and closes it only when the next screen has settled and lasted
  `min_moment_s`, so a brief switch to another window never splits it. Open moments are not read.
- *Speech that outlasts the screen:* the select gate waits for the transcript past the moment's end.
- *Topics spanning sections:* sections freeze only behind the horizon and at moment boundaries;
  select is given earlier moments' content. Whole-session consolidation is the synthesis pass.

Late data is never lost: everything captured stays on disk and the final run uses all of it.

**Stop.** `POST /live/{id}/end` (after the browser has flushed its uploads) starts the job:

1. **Finish the lanes.** Each lane flushes: the last audio window is cut wherever the audio ends,
   the detector closes the last moment (`finish`), the last moments are read and figures still
   rendering complete (figures not yet started are left to the render stage, which renders every
   figure at stop and reuses the live renders from its cache). The draft lane just stops; the write stage writes the tail. A lane still
   flushing after `IRIS_LIVE_FINISH_TIMEOUT_S` is cancelled and counts as failed.
2. **Ingest** as before: `audio.wav` and `frames.json` from the captured chunks.
3. **Promote.** If the audio, screen and select lanes finished without failing and none skipped
   captured input (a late chunk or frame), the live transcript, moments, alignment and selection
   are written as
   `transcript.json`, `moments.json`, `alignment.json` and `selection.json`, and the pipeline runs
   from write: frozen sections and live renders hit their caches, so only the tail is new work.
   Otherwise the full pipeline runs, as for an upload (with model caches still reused where inputs
   match); skipped input is recorded per lane, so a later version can re-run less. The draft
   lane's failure doesn't block promotion: its sections are only a cache.
4. **Synthesis** (planned, also for long uploads): see "Final synthesis" below.

A lane whose upstream lane failed (`upstream_failed`) stops making model calls for the rest of the
recording: nothing it makes can be promoted.

Live processing can be turned off with `IRIS_LIVE_PROCESSING=false`; a live session is then
processed after stop like an upload, as before.

### Final synthesis (planned)

Live notes are written forwards and never look back. After the notes are assembled and shown, a
synthesis pass consolidates them across the whole session, and its result replaces them; if it
fails, the assembled notes stay.

1. *Local sweep (no model calls):* compare moments and figures across the session to flag repeats
   (returning to an earlier slide, the same figure picked twice).
2. *Edit plan:* a model reads the section notes (with `[visual:<id>]` markers), an outline and the
   flagged repeats, and returns structured edits: merge a split worked example, apply a later
   correction, drop a repeated point, point duplicate figures at one. Only the sections the edits
   touch are rewritten; every visual marker must still resolve.

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
