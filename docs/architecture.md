# Iris architecture

Status: **draft**. Section 1 written; sections 2–6 to come, one at a time.

1. [Overview and the life of a session](#1-overview-and-the-life-of-a-session)
2. Pipeline stages *(next)*
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

1. **Faithful visuals.** Every image in the notes is a real frame from the session, or a crop of one.
   Nothing is redrawn or generated. A model may choose and caption a visual, but never draws one.
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
| **Pipeline** | Transcribe, find visual moments, pick visuals, write notes | Stages detailed in section 2 |
| **Job runner** | Runs a session's stages, in parallel where possible | In-process `asyncio`; single user, so no queue server |
| **Store** | Session files plus an index of sessions | Files under `sessions/<id>/`, metadata in SQLite |
| **API** | Create sessions, accept live input, stream progress, serve notes | FastAPI; typed TS client generated from its OpenAPI schema |
| **UI** | Upload, live view, notes view, library, export | Designed in `docs/frontend.md` |

### Key libraries

| Library | Used for |
|---|---|
| **FastAPI** + **Pydantic** | API and typed request/response models |
| **PyAV** | Decoding uploads and live chunks: audio resampled to 16 kHz mono, and video frames read faster than real time. Its wheels bundle FFmpeg, so there is no system install |
| **OpenCV** + **numpy** | Frame diffs, "has the screen settled", finding changed regions, cropping |
| **PySceneDetect** | Detecting slide and scene cuts |
| **RapidOCR** *(optional)* | Reading text on slides to match visuals to what was said |
| **openai** | Transcription, visual selection, notes |
| **faster-whisper** *(later)* | Local transcription, if cost or privacy calls for it |
| **SQLite** | Session index and status |
| **uv** | Python environment and dependencies |
| **openapi-typescript** | Generating the UI's typed API client |

### Life of a session: upload

1. **Create.** The UI uploads a file. The backend saves it as `sessions/<id>/source.<ext>` and starts a
   job.
2. **Ingest** (seconds, not real time). PyAV extracts 16 kHz mono audio and samples frames at a
   low fixed rate (around 1–2 fps), downscaled, each tagged with its timestamp.
3. **Transcribe and detect, in parallel.**
   - *Transcribe:* audio is split at silences into chunks, transcribed concurrently, and merged into
     one timestamped transcript.
   - *Detect moments:* frames are diffed locally to find where the screen changed and where it
     settled. Each moment is a stretch of time with one representative "settled" frame.
4. **Align.** Each moment gets the transcript spoken while it was on screen.
5. **Select.** A vision model looks at each moment's frame and words, and decides whether it belongs
   in the notes, which frame or crop shows it best, and a short caption. Moments are batched to keep
   calls and cost down.
6. **Write.** A model identifies the session type (meeting, lecture, demo) and writes the notes in a
   matching style, placing `[visual:<id>]` markers where each kept visual belongs.
7. **Assemble.** Markers are resolved to images. The notes are stored, shown, and exportable.

The UI receives progress events throughout: transcript chunks as they finish, moments as they are
found, and the notes as they are written.

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

- Redrawn, annotated or generated visuals (principle 1)
- Accounts, hosting, sharing links
- Desktop app and meeting bot (designed for, not built)
- Editing notes inside Iris

### Open questions for later sections

- Frame sampling rate for uploads: 1 fps or 2 fps? *(section 2)*
- Long sessions: one notes call, or per-section then merged? *(section 2)*
- Do you want speaker labels ("Alice: …") in meeting notes? *(section 5)*
- Which current OpenAI models give timestamps and fit the budget? *(section 5)*
- Server-sent events or WebSockets for progress and live input? *(section 4)*
