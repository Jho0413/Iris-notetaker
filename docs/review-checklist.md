# Review checklist

For authors before asking for review, and for the `code-reviewer` agent. Not every section applies
to every change; the reviewer says which ones it checked. Principles are those in
[`overview.md`](overview.md).

## 1. Correctness and edge cases

- The change does what the task says, and the docs' flows and decisions still hold.
- Units and ranges are right: seconds vs milliseconds, frame index vs time, inclusive vs exclusive,
  full-size vs downscaled pixels.
- Empty, tiny and huge input work: no frames, no speech, one segment, audio shorter than one
  transcription chunk, a 90-minute session (memory doesn't grow with length where it can be
  avoided).
- Malformed input fails clearly: corrupt or truncated media, an unknown format, a file from an
  older version.
- Boundaries: values exactly at a threshold, the first and last item, times at 0 and at the end.
- Non-ASCII titles, filenames and text (emoji, accents, CJK) survive.
- Async: no blocking work on the event loop (CPU-bound work goes through `asyncio.to_thread`);
  tasks are awaited or tracked; errors in concurrent work aren't swallowed; cancellation leaves no
  half-written files.
- Resources (files, media containers, HTTP clients) are closed.

## 2. Stages and caches

- A stage reads its inputs and writes only its own outputs and caches, named in
  `store/layout.py`; JSON is written with `write_model` and read with `read_model`.
- Running a stage twice gives the same result; a re-run after a crash recovers (principle 4);
  files left by an earlier run are cleaned up.
- A cache is reused only when model, prompt and inputs match; a damaged entry is redone, not
  trusted.
- Local work comes before model calls; models see only what survives it (principle 3).
- Media goes through PyAV; no system dependencies, no `subprocess` for media.

## 3. Stage edge cases

- **Ingest:** an audio-only file; video with no audio; several streams (the first is used); a
  non-zero start time, variable frame rate, rotated phone video, odd dimensions; frames sampled at
  the right times.
- **Transcribe:** cuts land in quiet audio, with no words lost or doubled at a cut; chunk length
  suits the model (`whisper-1` gives segment times, text-only models get short chunks);
  timestamps are session-absolute and sorted; silent audio; speech with no pause to cut at; one
  chunk failing while the others are cached.
- **Detect:** a cursor, caret, call thumbnails or compression noise make no new moment; content
  built up over time stays one moment, with its builds in order; scrolling stays one moment only
  while nearly all the earlier content stays in view (toolbars ignored), a larger scroll starts a
  new one; a switch of app or tab, erasing or covering starts a new one; a transition is never the
  representative frame; a static screen all session, or a playing video, doesn't explode the moment
  count.
- **Select:** element kinds, `render_as`, crops inside the frame and moment ids are checked;
  every figure gets a self-contained brief and a saved source crop; content captured in an earlier
  batch isn't listed again; prompt inputs are bounded, so a long session stays within context and
  budget.
- **Write:** all speech is used, with none dropped or doubled across sections, and an oversized
  segment is split without inventing times; worked examples come out step by step, each step's
  figure inside it; LaTeX survives Markdown processing; unknown or repeated `[visual:id]` markers
  are dropped, each figure is placed once.
- **Render:** a redraw reaches the notes only after its check passes; otherwise the source crop,
  `verified: false` in `renders.json` (principle 1); a source crop select chose is used as is;
  retries and concurrency are bounded by settings; a refusal or bad SVG is a failed attempt,
  retried with feedback; an API error gives the source crop at once, not cached; a blocking
  error fails the stage and cancels the figures still rendering; stale redraws are removed,
  source crops kept.
- **Assemble and export:** figures are placed by id (`::figure{#id}`) and point at their published
  files in `visuals/`, never at `*.source.jpg` (a fallback is a published copy); the export embeds
  every figure (SVG as PNG), keeps LaTeX and box and step lines whole, and no image path can
  leave the session folder.
- **Synthesis:** its output is used only while it matches the current draft and selection; a
  rewrite that loses a figure, maths, a decision, an action item or a box is rejected and its
  sections kept as written; a figure is dropped only as a confirmed duplicate; any failure keeps
  the assembled notes.
- **Notes format and edits:** model output and saved edits pass `notes_format` (unknown command
  or colour, too deep, unclosed, unknown figure, raw HTML, outside images); only the writer
  repairs, and only what needs no guess; old notes (`format_version` 1) still display;
  `notes/format.ts` and the editor follow the same rules, and a round trip keeps every construct;
  `notes_edits.json` is never written by a stage, and a re-run marks it stale, never drops it.

## 4. Model output and model calls

- Every model response is validated with Pydantic; unknown ids are dropped, never trusted.
- Model-written SVG goes through `sanitize_svg` before it is stored or rasterized.
- Model calls go through `get_openai`, use a model from `Settings`, and are bounded and cached so a
  re-run doesn't pay again.
- Units retry only temporary errors (`retry_temporary`); a blocking error stops the run at once
  (`gather_units`).
- Prompts treat session content (transcript, screen text, images) as data, never instructions.
- Nothing raw reaches the user: no exception text, model output or provider details in the API or
  UI; a known input problem uses `UserFacingError` with a `FailureMessage`. Event data carries no
  `problems` or raw error text.

## 5. API and live

- Unknown session → 404; an action in the wrong state → 409.
- No user-supplied path or filename can leave the session folder.
- Live audio chunks and frames that arrive late, twice or out of order are handled; a client
  that goes away mid-stream leaves the session recoverable.
- After an API change, `make gen-api` was run and `frontend/src/api/schema.d.ts` committed.

## 6. Desktop

- The main process stays thin: decisions live in pure functions in `desktop/src/`, with tests.
- Nothing widens what Iris windows may load, open or use without a stated reason.
- The key and the launch token never reach a page, a log or the engine's environment.

## 7. UI

- A user action's request goes through `act()`; destructive or leaving actions ask first
  (`ConfirmDialog`).
- User-facing wording comes from one place (`session/messages.ts` or the page's own module), not
  copies.
- Model-written Markdown renders without raw HTML; figures load as images; bad LaTeX shows in
  place.
- Loading, empty and error states are shown; the progress stream copes with errors, reconnects and
  unknown event types.
- Layouts use relative sizes and survive resizing, at desktop and phone widths, light and dark.

## 8. Tests

- Each behaviour the change adds or alters has a test, including its failure paths.
- Tests are deterministic and fast, and assert behaviour, not implementation detail.
- Real time may decide how long a test takes, never whether it passes. Time the code works with
  (the recording's clock, retry delays, fallbacks) is faked in the frontend (`mock.timers`,
  `performance.now`); waiting for work to happen waits for the event or condition with a
  deadline, not a fixed sleep followed by an assert.
- No real paid API calls and no network; model responses are faked.
- Media fixtures are generated in a temporary folder, not committed.

## 9. Quality

- Matches the conventions and practices in `CLAUDE.md`.
- Simple: no options, layers or abstractions without a current use.
- Reuse: nothing rebuilt that Iris or a dependency already provides; no near-copies.
- Scope: no unrelated refactors, renames or reformatting.
- Logic that decides something is in pure functions with tests; routes, components and the
  Electron main process stay thin.
- One source of truth: no copied constants, settings, file names or wording; tunable numbers are
  named constants or `Settings` fields, and new fields are listed in `backend/.env.example`.
- Names and comments explain why; no dead code left behind.
- A change in behaviour leaves nothing describing the old one: search for comments, docstrings,
  UI wording and files that still say or do what the code used to.
- No secrets in code or logs; no new dependency without a reason.

## 10. Docs

- Docs describe shapes, flows and decisions, not code detail; the detail lives in the code and its
  docstrings.
- A change that alters a flow or a decision updates that page in `docs/`.
- Module docstrings (what a stage reads and writes) match the code.
