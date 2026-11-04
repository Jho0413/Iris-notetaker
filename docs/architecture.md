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
8. **Synthesize.** While the assembled notes are readable, a pass across the whole session
   consolidates them (a worked example split across sections, a later correction, a slide returned
   to, a figure picked twice), and the polished notes replace them. If it fails, the assembled
   notes stay and the session still finishes (section 2, "Synthesize").

The UI receives progress events throughout (server-sent events): transcript chunks as they finish,
moments as they are found, figures as they are rendered, and the notes as they are written.

### Life of a session: live

The same work as an upload, done while the session is happening. What is done live *is* the final
work: at stop, only what was still open is new (the last transcription window, the screen shown at
the end and the tail of the notes), so the time from stop to finished notes depends on that, not on
how long the session was. A screen that stays open the whole session (one long worked example or
sketch) is only read at stop, with its figures rendered then, so such a session finishes about as
fast as an upload of it would (measured in `docs/roadmap.md`). Live processing is best effort: the
captured chunks stay the recording, and if anything live fails the session is processed like an
upload.

```
Browser                    API routes                 LiveProcessor (one per recording, asyncio tasks)
MediaRecorder 5 s chunks ─▶ POST /live/{id}/audio ─wake─▶ audio lane:  reorder ▸ decode ▸ cut at pauses ▸ transcribe ─┐
Frame ticker 1 fps ───────▶ POST /live/{id}/frame ─wake─▶ screen lane: hold back ▸ normalise ▸ MomentDetector ─────────┤
                                                                                                                       ▼
                                                          live timeline: transcript, moments, selection, watermarks
                                                                                                                       │
                                                          select lane: gate ▸ align ▸ select ▸ render (background) ◀──┤
                                                          draft lane:  horizon ▸ frozen sections + tail ▸ notes_draft ◀┘
Stop ─────────────────────▶ POST /live/{id}/end ───────▶ job: finish lanes ▸ ingest ▸ promote live outputs ▸ rest of PLAN
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
| `horizon` | `min(T_a, T_s)`: evidence before it is final (select publishes in time order: nothing it reads later starts before T_s) | (derived) |

Lanes wait on watermarks (an `asyncio.Condition`), never poll. A lane that has finished (after
stop) or failed counts as "through" any time, so nothing downstream waits forever.

| Lane | Input | Does | Publishes |
|---|---|---|---|
| **Audio** | `live/audio/*.webm` | Decodes the contiguous run of chunks into one PCM stream (`live/audio.pcm`, placed by timestamp exactly as ingest does). Cuts windows in the decoded audio (so never at a chunk edge) at the quietest point 5–15 s after the last cut, deciding each cut only once that whole range plus a second is decoded, so cuts depend on the audio alone and a restarted lane reuses its cached windows (`live/transcript_windows/`). Transcribes at most `IRIS_LIVE_TRANSCRIBE_CONCURRENCY` windows at once, each prompted (whisper-1) with the tail of the text committed when it is sent: normally the previous window. Commits windows in order; a window still failing after retries fails the lane | transcript segments, T_a, `TRANSCRIPT_CHUNK` |
| **Screen** | `live/frames/*.jpg` | Holds frames back `IRIS_LIVE_FRAME_HOLDBACK_S` (session time) to order them, samples them onto the `frame_fps` grid and normalises them with ingest's own helpers (size fixed by the first readable frame), writing `frames/` with ingest's names and bytes, and feeds `MomentDetector` the frames read back from `frames/`, as detect reads them | closed moments, T_m, `MOMENT_FOUND` |
| **Select** | closed moments + transcript | Gates each moment until T_a ≥ its end + `IRIS_LIVE_SPEECH_PAD_S`, aligns it, and reads it with the select stage's batch step and one call in flight (moments that pass the gate meanwhile go together in the next batch, up to `selection_batch_size`; visual ids continue across batches). Renders each figure in the background (`IRIS_LIVE_RENDER_CONCURRENCY`); a failed render is left to the render stage; the draft shows the source crop until it passes | contents, visuals, T_s, `VISUAL_SELECTED`, `VISUAL_RENDERED` |
| **Draft** | timeline | Packs the evidence published so far into sections like write does. A section that closes before the horizon is *frozen*: written once, with the cache key write computes for it at stop (`notes_sections/`). The *tail* after it, speech beyond the horizon included (so a long open moment doesn't stall the draft), is rewritten at most every `IRIS_LIVE_DRAFT_REFRESH_S`, and only where it changed. A payload whose call fails twice is given up until it changes; the write stage retries it at stop | `notes_draft.json`, `NOTES_UPDATED` |

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
   figure at stop; once the live selection is promoted, the live renders are its cache hits). The
   draft lane just stops; the write stage writes the tail. A lane still flushing after
   `IRIS_LIVE_FINISH_TIMEOUT_S` is cancelled and counts as failed.
2. **Ingest** as before: `audio.wav` and `frames.json` from the captured chunks.
3. **Promote** (`iris/live/promote.py`). What the live lanes finished replaces the stages that
   would redo it; what is written is exactly what the timeline holds (segments in its order, its
   `language`, the first window with speech's; moments in closing order; contents and visuals in
   reading order), since the frozen sections' cache keys depend on it:

   | Lanes at stop | Written from the timeline | The run continues with |
   |---|---|---|
   | `timeline.complete`: audio, screen and select finished, none failed, none skipped captured input | `transcript.json`, `moments.json`, `alignment.json` (align over those two), `selection.json` | write ∥ render, assemble, synthesize. Frozen sections and live renders hit their caches: only the tail section and figures not rendered live are new work |
   | Audio finished cleanly; screen or select failed or skipped input | `transcript.json` | detect, align, select, … (transcription isn't paid twice) |
   | Audio failed, skipped input or didn't run | nothing | the full plan, as for an upload (model caches still reused where inputs match) |

   Promotion also falls back to the transcript only if the live screen work doesn't hold together
   on disk after ingest (duplicate moment ids, a selection naming an unknown moment, a frame or
   source crop missing): the stages it would skip check those. The draft lane's failure doesn't
   block promotion: its sections are only a cache. `notes_draft.json` is deleted only if select
   runs again, so a promoted run keeps the live draft on screen until write replaces it.
   Promoted stages make no model calls. Each is reported as `stage_started`/`stage_finished` with
   `promoted: true`, and the events the stage itself would emit are replayed (`moment_found` per
   moment, `visual_selected` per figure), so the UI's progress and counts look as for a run; the
   transcript is fetched whole once transcribe finishes. A re-run (`POST /sessions/{id}/run`) has
   no processor and runs its plan as before.
4. **Synthesis**, as for uploads: see "Final synthesis" below.

A lane whose upstream lane failed (`upstream_failed`) stops making model calls for the rest of the
recording: nothing it makes can be promoted.

Live processing can be turned off with `IRIS_LIVE_PROCESSING=false`; a live session is then
processed after stop like an upload, as before.

**Replaying a recording.** `uv run iris replay-live <file>... [--url URL] [--speed X]`
(`iris/live/replay.py`) plays a recorded file into a running backend's live API as the browser
would: audio re-encoded with PyAV to one WebM/Opus stream posted as ~5 s byte ranges with `seq`,
screen frames as JPEG once a second with `t`, paced in real time (or X times faster), then
`POST /end`. It reports when the first transcript, moment, figure and draft events arrived, the
number of live draft refreshes, what was promoted, the time from stop to `done` and the cost (total
and after stop) from `/usage`. It makes paid calls through the backend; point the backend at a
scratch `IRIS_DATA_DIR`.

### Final synthesis

Notes are written forwards in sections (live, and for long uploads) and never look back. After the
notes are assembled and shown, a synthesis pass consolidates them across the whole session, and
its result replaces them; if it fails, the assembled notes stay.

1. *Local sweep (no model calls):* compare moments and figures across the session to flag repeats
   (returning to an earlier slide, the same figure picked twice).
2. *Edit plan:* a model reads the section notes (with `[visual:<id>]` markers), an outline and the
   flagged repeats, and returns structured edits: merge a split worked example, apply a later
   correction, drop a repeated point, point duplicate figures at one. Only the sections the edits
   touch are rewritten; every visual marker must still resolve.

Details, checks and cost are in section 2, "Synthesize".

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
- Long sessions: answered for now. Notes are written per section and joined, then consolidated by
  the synthesis pass. *(section 2)*
- Do you want speaker labels ("Alice: …") in meeting notes? *(section 5)*
- Which OpenAI models give timestamps? Only `whisper-1` returns timestamps, so it is the default;
  whether it fits the budget is part of cost tracking (`docs/roadmap.md`). *(section 5)*
- Progress uses server-sent events and live input uses HTTP POSTs per chunk. Revisit if live needs
  WebSockets. *(section 4)*

---

## 2. Pipeline stages

*Draft: the stages with notable behaviour. Ingest, transcribe, align and assemble are described in
their module docstrings for now.*

Order (`pipeline.PLAN`): ingest → transcribe ∥ detect → align → select → write ∥ render → assemble →
synthesize. Each stage appears once, so re-running from a stage (`plan_from`), the session's
`current_stage` and the UI's progress list need no special cases. A run that includes select
deletes `notes_draft.json` first: visual ids may change, so a draft written against the old
selection (live, or by an earlier run) is never served meanwhile. A run from write keeps it.

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
- Sections keep the first section's title and style and are joined in order. Consolidating them
  across the session is the synthesize stage's job.
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

### Synthesize

The last stage. Finishing is two steps: assemble publishes the notes as written, so they can be
read straight away (the UI shows *Polishing notes*), then synthesize consolidates them and the
pipeline runs assemble again inside the synthesize stage, so the polished notes replace the
assembled ones. Assemble uses `notes_synthesis.json` only while it was made from exactly the
current `notes_draft.json` and selection, so a re-run from an earlier stage never shows a stale
synthesis. A re-run from `synthesize` re-polishes without rewriting anything else.

- **Sections** are the notes split at their top-level (`#`, `##`) headings, outside code. Writing
  parts end at moment boundaries and usually open with a heading, so a topic split across parts
  shows up as two sections.
- **Local sweep** (OpenCV, in a thread, no model calls): the kept moments' representative frames
  and the figures' source crops are compared pairwise by a 64-bit DCT perceptual hash, then
  confirmed on blurred thumbnails (small mean difference, almost no strongly differing pixels).
  Figures are compared more strictly (one added line of working is a different figure), must have
  about the same shape, and are never paired within one moment, whose figures are separate builds
  by design. Groups are not transitive: a slow build (A like B, B like C) doesn't chain A and C.
  Groups of look-alikes are only hints for the model; unreadable images are skipped.
- **Edit plan:** one call to `IRIS_SYNTHESIS_MODEL` (default `gpt-5.5`) at
  `IRIS_SYNTHESIS_PLAN_REASONING_EFFORT` (default `low`) reads every section with its markers, an
  outline and the repeats, and returns structured edits: `merge`, `correct`, `drop_repeat`,
  `dedupe_figure`. They are validated: unknown section indexes and visual ids are dropped, a merge
  needs two sections, only `dedupe_figure` may remove markers, only among figures the sweep
  grouped (so image similarity alone never removes a figure, and neither does the model alone),
  the sections it changes are the ones holding the markers (computed locally), and a figure can't
  be both kept and dropped. A section a `drop_repeat` relies on to keep the point is never
  rewritten by another edit, so two repeats can't each lean on the other's copy.
- **Rewrite:** edits that touch overlapping sections are grouped, and each group is rewritten in
  one call (up to 4 at once, effort `IRIS_SYNTHESIS_REASONING_EFFORT`, unset: the notes effort),
  with read-only context sections. A group that only drops duplicate figures is applied locally
  (the markers are removed), with no call. A rewrite is accepted only if each of its visual markers
  is still there exactly once on a line of its own (minus the dropped duplicates), no images or
  HTML appear, its length stays between 40% and 150% (+2,000 characters) of the original (a
  section only dropped as a repeat, with a section that keeps the point and no other edit
  rewriting it, may shrink or empty, losing no more text than that section holds), it has no
  fewer decision, action-item, open-question or uncertainty words and task items, and any math it
  drops still appears elsewhere. A rejected group keeps its sections as written; the others are
  applied. Across groups, every equation in the notes must survive, and every marker must resolve
  once to a selected visual.
- **Cost:** write records how many parts it planned (`NotesDraft.parts`). Notes written in one part
  make no call, since the writer saw everything at once, unless the sweep found look-alike figures;
  then one planning call may only drop duplicates, applied locally. Notes in several parts cost one
  cheap planning call, which may return no edits, plus one rewrite per group of touched sections.
  A draft that doesn't record its parts (an older version) counts as one part only if it has one
  section. Notes over 200,000 characters are left as assembled. Plans and rewrites are cached in `synthesis_cache/`, keyed by model, prompt, schema and
  inputs; a damaged entry is regenerated and an invalid result is never stored.
- **Safety:** any failure (API error, refusal, invalid output, missing key) is logged and recorded
  in `synthesis.json` (`status: failed`, with the reason); the session still ends `done` with the
  assembled notes. `synthesis.json` also records the repeats found, the edits applied and the
  sections rewritten or rejected. `IRIS_SYNTHESIS=false` turns the pass off.
