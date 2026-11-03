# Iris frontend

Status: **approved** (3 November 2026), updated to match the build. This covers the screens, their states, how progress events reach
the UI, and the visual design. The pipeline and API behind it are in
[`architecture.md`](architecture.md); product facts the design relies on are in
[`frontend/PRODUCT.md`](../frontend/PRODUCT.md).

1. [How Iris works, from the user's side](#1-how-iris-works-from-the-users-side)
2. [Screens and flows](#2-screens-and-flows)
3. [Session states](#3-session-states)
4. [Progress events and partial results](#4-progress-events-and-partial-results)
5. [Visual design](#5-visual-design)
6. [Accessibility](#6-accessibility)
7. [What the UI needs from the API](#7-what-the-ui-needs-from-the-api)

---

## 1. How Iris works, from the user's side

Iris turns a session where something was shown into notes that include what was on screen
([architecture §1](architecture.md#1-overview-and-the-life-of-a-session)). For the user it is three
steps:

1. **Bring a session in.** Either upload a recording (**Upload**) or record one now (**Record
   live**: share a screen or tab and the microphone). A live recording is uploaded as it happens, so
   closing the tab never loses what was captured.
2. **Let Iris work.** The session page shows, in plain language, what Iris is doing: transcribing,
   finding what was shown, reading the screen, writing notes, redrawing figures. Partial results
   arrive as they're ready: the transcript fills in, figures appear as they're redrawn, and the notes
   show how far they've got. A one-hour upload takes a few minutes.
3. **Read and export.** The finished notes have typeset math, worked examples set out step by step,
   callouts, and the redrawn figures in the right places. **Export** downloads one Markdown file with
   the figures embedded. Every figure says whether it is a verified redraw or the original crop.

If anything fails, the session says what went wrong and offers **Try again**. This resumes from the
step that failed and reuses the work already done (and paid for)
([architecture principle 4](architecture.md#principles)).

The pipeline's internal stage names (ingest, transcribe, detect, align, select, write, render,
assemble) appear only in the **Details** panel. Everywhere else the UI uses these labels:

| Stage | Plain label |
|---|---|
| ingest | Preparing the recording |
| transcribe | Transcribing |
| detect | Finding what was shown |
| align | Matching speech to the screen |
| select | Reading the screen |
| write | Writing notes |
| render | Redrawing figures |
| assemble | Putting the notes together |

## 2. Screens and flows

```
          ┌──────────── Library (/) ────────────┐
          │  sessions list · empty state         │
          └──┬──────────────┬───────────────┬───┘
     Upload  │   Record live│               │ open a session
             ▼              ▼               ▼
      Upload (/upload)  Live (/live)  ──▶  Session (/sessions/:id)
             │              │ stop          processing ─▶ draft ─▶ finished
             └──────────────┴──────────────▶            └─▶ failed ─▶ Try again
```

A slim top bar is on every screen: the **Iris** wordmark (links to the library), **Library**, the
two primary actions **Upload** and **Record live**, and the theme switch (System, Light, Dark). While
a live recording runs, the top bar is replaced by the recording bar (see Live).

### Library (`/`)

**Purpose:** find a session and see at a glance which are finished, still working or need
attention.

- A dense list, newest first. Each row shows the title with its source (Upload or Live) beneath, a
  status chip in plain words (Processing · Writing notes, Finished, Failed, …) and the date. A
  **Length** column joins once the backend records it, and a **Cost** column once the cost API
  exists (§7).
- Row actions: open (the whole row is a link), **Export** (finished), **Try again** (failed) and
  **Delete** (any session not processing; asks for confirmation in a dialog).
- While any session is processing, the list refreshes every few seconds so chips stay current.

| State | What it shows |
|---|---|
| Loading | Five skeleton rows the height of real rows, so nothing jumps when data arrives |
| Empty | A short explanation of what Iris does and two large choices: **Upload a recording** and **Record live** |
| Error (backend unreachable) | "Can't reach the Iris backend", the command to start it (`make dev`) and **Retry** |
| Warning (no OpenAI key) | A banner: processing needs `OPENAI_API_KEY` in `backend/.env`; existing notes still open |

### Upload (`/upload`)

**Purpose:** get a recording into Iris with as little friction as possible.

- One drop zone, which is also a button that opens the file picker. It accepts video and audio
  files. After a file is picked it shows the name and size, with a **Change** action.
- An optional title, prefilled from the file name.
- **Make notes** submits the form. The upload shows a determinate progress bar (bytes sent);
  hour-long recordings are large. When it finishes, the app goes to the session page.

| State | What it shows |
|---|---|
| Idle | Drop zone and a disabled **Make notes** |
| File chosen | File name, size and type; **Make notes** enabled |
| Uploading | Progress bar with percent and **Cancel**; inputs disabled |
| Error | What failed (interrupted, rejected by the backend with its message, backend unreachable) and **Try again**, keeping the chosen file |
| Warning (no OpenAI key) | The same banner as the library, above the form |

### Live (`/live`)

**Purpose:** start recording, then stay out of the way. Users mostly start a session and check
later, so this screen is built around *is it still recording?* and *how do I stop?*.

- **Before starting:** an optional title, a plain explanation of what will be captured (one screen,
  window or tab, plus the microphone, with tab audio if shared), and **Start recording**.
- **Asking for permission:** "Choose what to share in your browser's dialog".
- **Recording:** a recording bar pinned across the top of the window. It shows a red dot, the label
  **Recording**, the elapsed time in tabular numerals, capture status in words (Screen shared ·
  Microphone on) and **Stop and make notes**. Below it, centred and calm: a large timer, a small
  screen preview, and one line on what happens when you stop. Two disclosures, closed by default,
  show what live processing has made so far (§4). Each has a fixed height whatever it holds, so
  nothing arriving moves the page, and mounts its contents only while open:
  - **Show live transcript:** lines from `transcript_chunk` events, in time order. Opening it
    starts at the latest line; it follows new lines only while the reader is at the bottom (within
    24px), otherwise **Jump to latest** appears (`src/session/follow.ts`).
  - **Show notes so far:** the live draft (`GET /notes/draft`), rendered like the final notes, with
    figures as the original crop until they're redrawn. Refetched on every `notes_updated`; until
    the first one it says a draft appears once enough has been said and shown.
- **Screen sharing ended** (the user stopped sharing from the browser bar), the microphone
  disconnected or the browser's recorder failed: Iris stops and makes notes, the same as **Stop and
  make notes**. The capture module reports these through its `onEnded` callback.
- **Stopping:** "Finishing the upload…" with the stop button disabled, then the app goes to the
  session page, which takes over (processing). If capture stopped but the backend couldn't be told
  to make notes (uploads still queued, or the finish request failed), the page says **Recording
  stopped**, keeps the error in view and offers **Try again** (which resends what is still queued,
  then the finish request) and **Open the session**. Nothing captured is lost while the tab stays
  open.
- **Leaving while recording:** the browser warns before closing the tab, and in-app navigation asks
  for confirmation, because leaving ends the recording.

| State | What it shows |
|---|---|
| Permission denied or cancelled | Which permission was refused and how to allow it, **Try again**; the empty session that was created is deleted |
| Capture failed | The capture module's message (or the browser's error in plain words) and **Try again** |
| Warning (no OpenAI key) | The same banner as elsewhere, before starting |

Capture itself (`src/capture/`) is owned by live-capture work. This screen only calls
`LiveCapture.start()` and `stop()`, shows the returned screen stream as the preview, and passes an
`onEnded` callback that stops. It holds the capture from before `start()`, so leaving the page
during a permission prompt cancels the start; the capture module then releases what was granted
and deletes the empty session.

### Session (`/sessions/:id`): processing and finished notes

**Purpose:** follow a session from processing to finished, then read it. The layout stays the same
from the first event to the finished notes, so nothing jumps when the notes land.

```
┌ top bar ─────────────────────────────────────────────────────────────────────────────┐
├──────────────────────────────────────────────────────────────────────────────────────┤
│ Library › Algebra lecture                                     [Export] [Try again]  │
│ LECTURE · Upload · 12:31 · Today 14:02         ● Processing · Redrawing figures 3/5  │
│ ▕═══■════■══════■═════════════░░░░░░░░░░░░░░░▏  session ribbon (signature, §5)       │
├───────────────────────────────────────────────────┬──────────────────────────────────┤
│  Notes │ Transcript 42 │ Figures 3/5              │  Progress                        │
│ ─────────────────────────────────────────────────│   ✓ Preparing the recording      │
│                                                   │   ✓ Transcribing                 │
│   reading column (max 68ch)                       │   ● Writing notes · 2 of 4        │
│                                                   │   ● Redrawing figures · 3 of 5   │
│                                                   │   ○ Putting the notes together   │
│                                                   │  On this page (finished)         │
│                                                   │  Cost (slot, §7)                 │
│                                                   │  Details ▸ stages, times, errors │
└───────────────────────────────────────────────────┴──────────────────────────────────┘
```

- **Header:** a breadcrumb back to the library, the title, the meta line (session type once known,
  source, length, date), the status chip, and the actions: **Export** (once notes exist) and **Try
  again**, **Resume** or **Finish and make notes** depending on the state (§3).
- **Session ribbon:** a thin bar spanning the reading column that represents the session's length.
  See §5.
- **Tabs:** **Notes**, **Transcript** (segment count) and **Figures** (rendered of selected). Tabs
  keep their content mounted, so switching never refetches or loses scroll position.
- **Rail (right, 300px):** **Progress** (plain-language steps, the running ones with counts), **On
  this page** (the notes' section headings, once there are notes), **Cost** (slot) and **Details**
  (closed by default: stage names, start and finish times, durations, the raw error, event stream
  status).
- **Below 1120px** the rail moves above the tabs as a one-line summary ("Writing notes 2/4 ·
  Redrawing figures 3/5"), with **Details** expanding it.

**Notes tab.** While recording or processing, this shows the draft: the notes as far as they've
got (§4), under a note that it fills in as Iris works and that figures show the original crop until
they're redrawn. Once finished, it is the reading view: session type, title, then the notes at a
68-character measure.

**Transcript tab.** Segments with start times (`12:04`), in time order. During processing, chunks
arrive out of order (they're transcribed concurrently) and are inserted in place without moving the
scroll position. When the full transcript exists, it replaces the partial one.

**Figures tab.** A grid of every figure selected. A figure being redrawn shows its caption over a
fixed 4:3 placeholder. When it's done, the image replaces the placeholder in the same box, with a
badge: **Verified redraw**, **Screenshot** or **Original crop · not verified**. Selecting a figure
opens it full size in a new tab.

| State | What it shows |
|---|---|
| Loading | Header and rail skeletons, a reading-column skeleton |
| Not found | "This session doesn't exist" and a link back to the library |
| Backend unreachable | Inline error with **Retry**; anything already loaded stays visible |
| Event stream reconnecting | "Reconnecting…" in the rail's Details; the page keeps what it has |
| Processing, nothing yet | Notes tab: "Notes appear here as they're written", with the current step |
| Finished, no figures | Figures tab: "No figures in this session. Iris found nothing on screen worth redrawing" |
| Finished, no speech | Transcript tab: "No speech was transcribed" |

## 3. Session states

The backend's `session.status` is `created`, `recording`, `processing`, `done` or `failed`. The UI
combines it with what the page knows (events, whether this tab is the one recording) into the
states below. Each has a text label as well as a colour (§6).

| UI state | When | Chip | Primary action |
|---|---|---|---|
| **Recording** | `recording`, and this tab is capturing | ● Recording (red, pulsing) | Stop and make notes |
| **Recording not stopped** | `recording`, but no capture in this tab (tab closed, browser crashed) | Recording not stopped (neutral) | Finish and make notes (`POST /api/live/{id}/end`), or Delete |
| **Starting** | `created` | Starting… (neutral) | none |
| **Processing** | `processing`, no notes yet | Processing · current step (accent) | none |
| **Draft** | `processing`, and at least one notes section is written | Draft · 2 of 4 sections (amber) | none |
| **Stalled** | `processing`, but a connection attempt to the event stream delivers no event within 5 s, so the run isn't active (the backend restarted) | Stopped unexpectedly (neutral) | Resume (`POST /run?from_stage=<current_stage>`) |
| **Finished** | `done` | Finished (green) | Export |
| **Failed** | `failed` | Failed · step (red) | Try again (`POST /run?from_stage=<failed stage>`) |

Notes on these states:

- **Failed** shows the error in plain words above the tabs ("Writing notes failed: the model
  service was unavailable"), with the raw message in Details. If notes from an earlier run exist,
  they stay readable below, marked "from an earlier run".
- **Retry and resume reuse caches**, so repeating a step costs little. Both buttons say so in their
  description ("Resumes from Writing notes; finished steps are reused").
- **Stalled** works because the backend replays a running session's event history on connect
  (`events.py`). A connection that replays nothing means the run isn't in this backend process.
  Silence is timed from each connection attempt, not from the stream's `open`: through the Vite dev
  proxy the headers only arrive with the first byte.
- A run started by **Try again** or **Resume** begins with a `status: processing` event. The UI
  clears its previous run state at that point, so old stage results don't mix with new ones.

## 4. Progress events and partial results

The session page subscribes to `GET /api/sessions/{id}/events` (server-sent events) through
`useSessionStream` (`src/session/`). Events are reduced into one **session view state** by a pure,
tested function (`src/session/state.ts`).

- **Idempotent:** the stream replays history on every reconnect. Replays are dropped by key in the
  hook, and every event is keyed in the reducer as well (stage name, chunk index, figure id), so a
  replayed event changes nothing.
- **Runs:** a run starts with `status: processing`. Events older than the current run's start are
  ignored, so a run resumed after a backend restart never mixes with the one that died.
- **Batched and incremental:** new events are handed over every 50 ms and applied to the current
  state, so a replayed history of up to 1,000 events renders once rather than re-reducing it per
  event. The connection logic lives in `src/session/stream.ts` and is tested with a fake
  EventSource.
- **Reconnects:** the browser retries dropped connections. When it gives up (a failed response,
  such as while the backend restarts), the hook reconnects itself after 1, 2, 5, then every 10
  seconds.

| Event | Data used | UI effect |
|---|---|---|
| `status` | `status` | Updates the chip and actions; `processing` starts a new run (clears run state); `done` fetches the final notes; `failed` fetches the session for its error |
| `stage_started` | `stage`, `at` | Step becomes running; records its start time for Details; the header names it |
| `stage_finished` | `stage`, `at` | Step becomes done; duration shown in Details; after `transcribe`, fetch the full transcript |
| `transcript_chunk` | `chunk`, `segments` | Segments stored by chunk index and shown in time order; ribbon shows how much is transcribed |
| `moment_found` | `id`, `start`, `end` | Count shown in the Finding-what-was-shown step; tick on the ribbon |
| `visual_selected` | `id`, `caption`, `frame.t`, `render_as` | Figure placeholder added; ribbon marker (hollow); render total updated |
| `visual_rendered` | `id`, `path`, `render_as`, `verified` | Placeholder replaced by the image; badge from `verified` and `render_as`; ribbon marker filled |
| `notes_updated` | `final`, `completed_parts`, `total_parts`, event time | `final: false` → Draft, "2 of 4 sections", draft refetched (every such event, also a live tail rewrite with the same counts; `live: true` marks the live draft lane's); `final: true` → fetch notes |
| `error` | `message` | Failed banner text (the session's `error` is the source of truth) |
| unknown type or malformed | none | Ignored (`decodeSessionEvent`) |

**When there are no events.** Event history lives in backend memory and is lost on restart. A
finished session opened later gets everything over REST instead: `GET /notes`, `GET /transcript`,
and figure times from `notes.visuals[].frame.t`. Only stage times and verified badges are missing.
Details then says "Step times are only available for runs since the backend started".

**No disruptive updates.**

- New content never moves what the user is reading. The transcript inserts out-of-order chunks
  without scrolling.
- The live transcript follows new lines only while the reader is at the bottom; otherwise a **Jump
  to latest** button appears.
- Figure boxes keep a fixed 4:3 size until the image loads.
- When the draft is refetched, the reader keeps their scroll position: sections are added and
  the open last one rewritten at the end, never above what is being read. A failed refetch keeps
  the draft already shown.
- The draft is fetched once when the page loads (recording or processing) and then on each
  `notes_updated` (`draftRefreshKey` in `src/session/draft.ts`); replayed events don't refetch.
- Status changes are announced politely to screen readers; transcript chunks are not.

## 5. Visual design

**Direction (chosen):** clean and neutral, with one accent and a simple, modern dark mode. The app
around the notes should get out of the way (an Operate surface); the notes themselves should be a
pleasure to read (a Read surface).

### Theme

- **Light, dark or system.** The switch in the top bar stores the choice in `localStorage` and sets
  `data-theme` on `<html>`. Before the app loads, an inline script applies the stored choice, so
  dark mode never flashes white.
- **Every colour is a CSS custom property**, so the themes differ only in tokens. No component
  hard-codes a colour.
- **Neutrals are cool and lightly tinted toward the accent**, never pure grey. The rail and top bar
  use a second neutral layer, set apart from the content surface.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f6f6f9` | `#0e0f13` | Page ground |
| `--surface` | `#ffffff` | `#16171d` | Reading column, cards, inputs |
| `--surface-2` | `#eeeef3` | `#1c1d24` | Rail, top bar, table headers, code |
| `--border` | `#dedee6` | `#2b2c36` | Hairlines, dividers |
| `--text` | `#17171c` | `#ececf2` | Body, headings |
| `--text-2` | `#5b5b68` | `#a2a2b0` | Meta, captions, labels (≥ 4.5:1 on `--surface`) |
| `--accent` | `#4b44d6` | `#8f8cff` | Primary buttons, links, focus, current step |
| `--accent-soft` | `#ecebff` | `#25244a` | Selected tab, problem card, processing chip |
| `--recording` | `#c92a2a` | `#ff6b6b` | Recording dot and bar, Failed |
| `--success` | `#1d7a3e` | `#5fd38a` | Finished, answer card |
| `--warning` | `#8f5a00` | `#f0b44c` | Draft, watch-out callout, sharing ended |
| `--info` | `#2457c5` | `#7fa6ff` | Key-idea callout |

Each state colour also has a `-soft` background version for chips and cards. Text-on-background
pairs are checked to WCAG AA (4.5:1 for text, 3:1 for icons and focus rings) in both themes.

### Typography

- **One family:** the system UI stack (`-apple-system, BlinkMacSystemFont, "Segoe UI", system-ui,
  sans-serif`) for the app and the notes. No web fonts, nothing to download. KaTeX keeps its own
  math fonts.
- **Fixed rem scale**, ratio about 1.2: 12 · 13 · 14 (UI base) · 16 · 17 (notes body) · 20 · 24 ·
  30 px.
- **Notes body** is 17 px with 1.7 line height at a 68-character measure. Headings are tight (1.2
  line height, −0.01 em tracking) with more space above than below.
- **Tabular numerals** for timers, timestamps, counts and percentages, so they don't jitter.
- **Weights:** 400 body, 500 labels, 600 headings and buttons. No ALL-CAPS except the small
  session-type label (tracked +0.06 em).

### Spacing, shape and layout

- **4 px base unit; steps:** 4 · 8 · 12 · 16 · 24 · 32 · 48 · 64.
- **Shape:** 6 px radius on controls and chips, 10 px on panels and cards, 8 px on figure plates.
  Hairline borders instead of shadows; one soft shadow only for the dialog and the recording bar.
- **Laptop widths:**
  - 1280–1600: reading column plus a 300 px rail, centred, with a 1200 px max width.
  - 1024–1120: the rail collapses above the tabs.
  - Below 1024: one column, still usable down to 768.
  - The library list is capped at 960 px.

### Notes content

- **Math:** inline math sits on the text baseline. Display math gets its own line with 12 px above
  and below and scrolls sideways when it's wider than the column. Invalid LaTeX renders in place as
  the source in `--recording` colour, so the page never breaks.
- **Step cards:**
  - **Problem:** `--accent-soft` background, a 1 px `--accent-line` border, and the label "Problem"
    in the accent colour.
  - **Step n:** no box. Consecutive steps are joined by a 1 px accent path with a ring at each
    step, so a worked example reads as one sequence. The step title (in the accent colour) leads,
    and the working and its figure sit inside the step.
  - **Answer:** `--success-soft` with a 1 px `--success-line` border and a `--success` label.
    Callouts after the answer close the card, because they're about the whole example.
- **Callouts:** **Key idea** uses `--info-soft` with a 1 px `--info-line` border and an `--info`
  label; **Watch out** uses `--warning-soft`, `--warning-line` and a `--warning` label. The label is
  bold, the body plain. No icons; the label carries the meaning.
- **Figures:**
  - Centred on a **plate**: a white rounded rectangle with a hairline border, 12 px padding and max
    height 520 px. The plate stays white in dark mode because redraws and screenshots are drawn on
    white, and inverting them would falsify them.
  - The caption sits below in `--text-2` at 14 px, followed by when the figure was shown ("Shown
    at 1:14") and its provenance badge, the same as in the Figures tab.
  - Provenance badges: **Verified redraw** (a redraw only reaches the notes after passing its
    check, so this is known from the notes alone), **Screenshot** or **Original crop · not
    verified**. A screenshot in the finished notes may be either of the last two, so those badges
    show only when this run's events say which; otherwise there is no badge.
- **Tables and code:** tables use hairline rows with a `--surface-2` header, and scroll sideways
  when wide. Code blocks sit on `--surface-2` in the system monospace font at 14 px.

### Signature: the session ribbon

One element ties the UI to what Iris does: **the session ribbon**, a thin horizontal bar under the
session header that represents the session's length.

- **Scale:** a baseline with a tick every tenth of the session, the start and end times, and a
  label ("3 figures, placed where they were shown").
- **Figures:** each one is a small pin at its source time (`frame.t`). It is hollow while
  being redrawn and filled once done, amber if it's an unverified original crop. Hovering or focusing
  a marker shows its time and caption; activating it scrolls the notes to that figure, or opens the
  Figures tab at it while the notes aren't ready.
- **Finished notes:** the ribbon shows at a glance where in the session each visual came from: what
  was shown, and when.
- **Accessibility:** markers are buttons in a labelled group ("Figures by time in the session").

The ribbon's length is the transcript's last end time, so it appears once transcription has
finished and there is at least one figure. It is the only decorative element in the app.

### Motion

- **Transitions:** 150–200 ms, for state changes only (tab underline, chip colour, a figure fading
  in over its placeholder, new transcript lines).
- **Recording dot:** pulses slowly.
- **No page-load choreography.**
- **Reduced motion:** with `prefers-reduced-motion`, everything is instant and the dot is solid.

## 6. Accessibility

- **Keyboard:** every action is a native `button`, `a` or `input`.
  - A skip link jumps to the main content.
  - Tabs follow the ARIA tabs pattern (arrow keys move between tabs, Tab enters the panel).
  - The delete confirmation is a native `<dialog>`: focus is trapped inside, Escape closes it, and
    focus returns to the button that opened it.
  - The upload drop zone is a labelled file input.
- **Focus:** a 2 px `--accent` outline with a 2 px offset on `:focus-visible`, in both themes.
- **Contrast:** AA in both themes (§5). State is never shown by colour alone: chips have words, steps
  have ✓ / ● / ○ glyphs plus text, and figure badges have words.
- **Announcements:** an `aria-live="polite"` region announces status changes ("Writing notes",
  "Notes finished", "Processing failed"); errors use `role="alert"`.
- **Titles:** each page sets `document.title` ("● Recording 12:04 · Iris" while recording).

## 7. What the UI needs from the API

All of the above uses the existing API and event types, except:

| Need | Status | Until then |
|---|---|---|
| `visual_rendered` in the client's event list (`src/api/eventMessage.ts`) | Frontend fix in this work: it is in the schema but was missing from the list, so rendered figures were dropped | n/a |
| Draft notes text: `GET /api/sessions/{id}/notes/draft` (returns `NotesDraft`, 404 when there is none) | Done with live processing. Like `GET /notes`, figure markers are resolved by the backend: to the redrawn figure once `renders.json` has it, to the source crop until then (all through recording); markers for figures not selected yet are dropped. While recording it is the live draft lane's, while processing the write stage's | n/a (`fetchDraft` in `src/session/draft.ts`) |
| Ending a crashed live session with gaps (`endLiveSession` with `allow_gaps`) | Done in the live-capture PR. Without a body, `POST /api/live/{id}/end` skips the chunk check and ends with what arrived (missing audio becomes silence) | **Finish and make notes** calls `POST /api/live/{id}/end` without a body, which is already correct for a crashed session; `endLiveSession` is only needed to check the chunk count |
| Per-session cost and time per stage | Cost-tracking work in progress | The rail's **Cost** slot and the library's **Cost** column are designed but not built; stage times come from events |
| Verified status for finished sessions without events (`renders.json` via the API) | Not requested yet | Badges show only when known from this run's events |
| `session.duration_s` set at ingest | Not set by the backend today | The session page takes the length from the transcript's last segment; the library has no Length column |
