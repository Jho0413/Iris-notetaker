# Overview

What Iris does, the goals and principles behind it, and how the system is put together. The other
architecture docs go deeper:

- [`pipeline.md`](pipeline.md): the life of an upload and how each stage works
- [`live.md`](live.md): live capture, and processing a session while it records
- [`errors.md`](errors.md): how failures are classified, retried and shown
- [`desktop.md`](desktop.md): the macOS app: the `app://` handler, the token, the backend's
  lifecycle, security and packaging

## What Iris does

Iris takes a session where someone shows things while talking (a meeting, a lecture, a demo) and
writes notes that include **what was on screen**, placed next to what was said about it. Audio-only
notetakers lose the slide, the whiteboard sketch or the diagram the conversation was about. Iris keeps
it.

## Goals

- **Users:** one person, running locally on a laptop. No accounts or hosting.
- **Input:** an uploaded recording, or live capture (mic plus screen share) in the browser or in
  the macOS desktop app, which records any screen or window with system audio. Later, integrations
  such as meeting platforms.
- **Visuals:** anything shown on screen (handwriting, sketches, diagrams, slides) is rebuilt as a
  clean visual: typeset maths, plotted graphs, rendered diagrams, tables, illustrations, or a
  cleaned-up screenshot where the screen is already clean. No restriction on visual type.
- **Notes:** the style adapts to the content (meeting, lecture or demo). Worked examples are written
  step by step, with the visual for each step. Shown in the app, editable there, and exported from
  it as a PDF; other clients get Markdown from the engine's API.
- **Performance and cost:** typical sessions are 30–90 minutes. A 1-hour upload produces notes
  within a few minutes; a live session's notes are finished soon after stop. Processing costs
  $0.50–2 per hour of session.

## Principles

1. **Professional visuals, accurate to the session.** What was shown on screen is rebuilt as a clean
   visual, with no restriction on visual type. Redraws must match what was shown and said: each is
   verified against the source frames and transcript, and source frames stay on disk so any visual
   can be regenerated. If no redraw passes, the source crop is shown instead.
2. **One pipeline, many sources.** An uploaded file, live capture in the browser or the desktop
   app, and later a meeting bot all produce the same thing: timestamped audio and frames.
   Everything downstream is unaware of where they came from.
3. **Local work first, model calls last.** Finding what changed on screen is free OpenCV work on the
   machine. Models only see the few moments that survive it.
4. **Never lose a session.** Every stage writes its output to disk as it goes. A crash, a closed tab or
   a bad prompt means re-running a stage, not re-recording.
5. **Small and measurable.** Each stage reads files and writes files, so it can be run and tested on its
   own, and quality is judged against a fixed set of real recordings.

## System shape

```
┌─ Web UI (TypeScript) ──────────────┐          ┌─ Backend (Python, FastAPI) ───────────────────┐
│  upload · live capture · notes     │── HTTP ─▶│  API ──▶ job runner ──▶ pipeline stages        │
│  view · library · export           │◀─ events─│                              │                 │
└────────────────────────────────────┘          │                   sessions/<id>/  + SQLite     │
                                                └──────────────────────────────┬────────────────┘
                                                                               ▼
                                                                         OpenAI API
```

Everything runs on your laptop. The UI and the backend are two local processes. The backend is the
engine: any client can feed it a recording and read back the notes as Markdown.

### The desktop app

`Iris.app` (macOS, `desktop/`) is the same UI and backend with no terminal: Electron shows the UI
and starts a bundled backend (a standalone Python with Iris installed) as its child process.

```
Iris.app
├── Electron main process
│     • app://iris/api/*  → http://127.0.0.1:<port>/api/* with X-Iris-Token, streamed both ways
│     • app://iris/<file> → the built UI; any other path → index.html (client-side routes)
│     • starts the backend: python3 -I -m iris serve --port <free port> --secrets-from-stdin
│         stdin: the OpenAI key (from the Keychain-encrypted store) and the API token, one JSON line
│         env: IRIS_DATA_DIR, IRIS_PARENT_PID; data and logs in ~/Library/Application Support/Iris
├── Window: app://iris/, the web UI unchanged
└── Resources/backend/python, Resources/frontend
```

- **The backend never serves the UI.** Electron serves it from the `app://` scheme and forwards
  `/api` to the backend, so the UI still loads (and says the engine isn't running) if the backend
  stops.
- **A per-launch token** guards the API: the backend refuses (401) any request without it, so
  other local processes and web pages can't use it. The app adds it to every forwarded request;
  the page never sees it, or the port. The token and the key reach the backend on its stdin, not
  its environment, which other processes of the user can read.
- **The backend can't outlive the app.** It stops on quit and, if the app crashes or is
  force-quit, exits on its own once the app's process is gone (`IRIS_PARENT_PID`).
- **Capture** goes through the UI's own `getDisplayMedia` call, answered by the macOS system picker
  (any screen or window) with system audio.

Details, decisions and measurements: [`desktop.md`](desktop.md).

## Components

| Component | Job | Notes |
|---|---|---|
| **Sources** | Turn an input into timestamped audio and frames on disk | File upload, live capture (browser or desktop app) |
| **Pipeline** | Transcribe, find visual moments, read screens, rebuild figures, write notes | [`pipeline.md`](pipeline.md) |
| **Job runner** | Runs a session's stages, in parallel where possible | In-process `asyncio`; single user, so no queue server |
| **Store** | Session files plus an index of sessions | Files under `sessions/<id>/` (`store/layout.py` lists every file), metadata in SQLite |
| **API** | Create sessions, accept live input, stream progress, serve notes | FastAPI; typed TS client generated from its OpenAPI schema |
| **UI** | Upload, live view, notes view, library, export | [`frontend/DESIGN.md`](../../frontend/DESIGN.md) |

## Sources are pluggable

A source has one job: write timestamped audio and frames into a session. The pipeline only reads what
is in the session, so adding a source never touches the pipeline.

| Source | When | How |
|---|---|---|
| File upload | Built | PyAV over the file |
| Browser live | Built | `getUserMedia` / `getDisplayMedia`, chunks posted to the backend ([`live.md`](live.md)) |
| Desktop capture | Built (macOS) | The desktop app: the browser live source, with any screen or window and system audio from the macOS picker |
| Meeting platforms | Later | Zoom, Meet or Teams integrations that stream the call |

## Key libraries

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
| **Tiptap** (ProseMirror) + **@tiptap/markdown** | Editing notes in place: boxes, steps, figures and formulas as nodes, read from and written back to the notes format |

## Not built yet

- Accounts, hosting, sharing links
- Meeting integrations; the desktop app on Windows, and signed for distribution

## Open questions

- Frame sampling rate for uploads: 1 fps or 2 fps? Currently 1 fps.
- Do you want speaker labels ("Alice: …") in meeting notes?
- Only `whisper-1` returns timestamps, so it is the default transcription model; whether it fits the
  budget is part of testing the engine ([`../roadmap.md`](../roadmap.md)).
- Progress uses server-sent events and live input uses HTTP POSTs per chunk. Revisit if live needs
  WebSockets.
