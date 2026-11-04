# Review checklist

Used by authors before asking for review and by the `code-reviewer` agent. Not every item applies to
every change; the reviewer says which sections it checked.

## 1. Correctness

- Does the code do what the task and `docs/architecture/` say, including the stage contract
  (reads its inputs, writes only its own outputs and caches as named in `store/layout.py`)?
- Off-by-one and unit errors: seconds vs milliseconds, frame index vs timestamp, inclusive vs
  exclusive ranges, pixel coordinates vs downscaled coordinates.
- Async: no blocking I/O or CPU work on the event loop (decoding, OpenCV, SVG rasterizing go through
  `asyncio.to_thread`); tasks are awaited or tracked; exceptions in concurrent work are not swallowed;
  cancellation leaves no half-written files.
- Resources: files, containers (`av.open`), and HTTP clients are closed (context managers).

## 2. Edge cases (all code)

- Empty input: zero frames, zero segments, zero moments, zero visuals, empty file, zero-length audio.
- Tiny input: one frame, one segment, audio shorter than one chunk.
- Large input: 90+ minute sessions. Memory must not scale with session length where avoidable
  (stream frames, don't load all into RAM).
- Malformed input: corrupt or truncated media, unknown container, a JSON file from an older version.
- Boundaries: values exactly at thresholds; first and last element; timestamps at 0 and at the end.
- Re-runs: running a stage twice gives the same result and doesn't duplicate output; a stage re-run
  after a crash recovers (principle 4). Caches (`transcript_chunks/`, `selection_batches/`,
  `notes_sections/`, `render_cache/`) are reused only when model, prompt and inputs match, and a
  damaged entry is regenerated, not trusted. Files left by an earlier run are cleaned up.
- Non-ASCII: titles, filenames and transcript text with emoji, accents, CJK.

## 3. Stage-specific edge cases

**Ingest**
- Audio-only file; video with no audio; multiple audio or video streams (first is used).
- Non-zero stream start time; variable frame rate; rotated phone video; odd dimensions.
- Very long file decoded faster than real time; frames sampled at the right timestamps.

**Detect**
- Cursor movement, blinking caret, video-call thumbnails, compression noise: no new moment.
- Content added over time (slide builds, handwriting line by line) stays one moment, with builds in
  order and the added region recorded for each.
- Vertical canvas scrolling stays one moment while earlier content is still on screen; content
  scrolled fully off screen starts a new moment. Static toolbars don't break scroll matching.
- App or tab switches, erasing or covering content: a new moment.
- Transitions/animations never chosen as the representative frame.
- Returning to an earlier slide; static screen for the whole session; constantly changing screen
  (a playing video) does not explode the moment count.

**Transcribe**
- Chunk cuts land in silence; no words lost or duplicated at chunk boundaries.
- Chunk length matches the model: `whisper-1` returns segment timestamps; text-only models get short
  chunks, each one segment.
- Timestamps are session-absolute after merging; segments sorted.
- Silent audio; audio that is all speech (no silence to cut at); one chunk fails while others succeed.

**Select (screen reading)**
- Model output validated: element kinds and `render_as` values allowed, crops inside frame bounds,
  moment ids that exist, required fields present. Invalid output is rejected or repaired, never
  trusted.
- Every figure gets a self-contained description (the brief for rendering and checking) and a saved
  source crop.
- Content already captured in an earlier batch isn't listed again; the same visual discussed in
  different contexts is kept where it adds something.
- Prompt inputs bounded: batch size, transcript and OCR characters per batch (long sessions don't
  exceed context or the cost budget).

**Write**
- All transcript text is used; sections split long transcripts without dropping or duplicating
  speech, and oversized segments are split without inventing timestamps.
- Worked examples and processes come out step by step, with each step's figure inside it.
- Math stays valid LaTeX through markdown processing (no escaped or mangled `$...$`).
- Unknown or repeated `[visual:id]` markers are dropped; each visual is placed once; never more
  visuals than were selected.

**Render**
- A redraw appears in the notes only if it passed the check against the source crop and brief. If
  no renderer passes, the source crop is used and recorded as `verified: false` in `renders.json`.
- Requested screenshots are used as is; the fallback chain (retry with feedback, then the other
  renderer) is bounded by `IRIS_RENDER_ATTEMPTS`, and concurrency by `IRIS_RENDER_CONCURRENCY`.
- Model-written SVG goes through `sanitize_svg` before it is stored or rasterized: no scripts,
  `foreignObject`, external references, doctypes or entities; a `viewBox` is required.
- Refusals and invalid SVG count as failed attempts and are retried with feedback. An API error
  gives the source crop at once (unverified, not cached, reason in `problems`); a blocking error
  fails the stage and cancels the figures still rendering.
- Stale rendered visuals from earlier runs are removed; source crops are kept.

**Assemble / export**
- Notes link to rendered visuals, never to `*.source.jpg` directly.
- Markdown export embeds every figure (SVG as PNG), keeps LaTeX intact, and image paths can't
  escape the session folder.

**Errors (all stages)**
- Model calls go through the error policy (`docs/architecture/errors.md`): a unit
  retries only `TEMPORARY` errors (`retry_temporary`), never `BLOCKING` ones, and concurrent
  units stop at once on a blocking error (`gather_units`, a `TaskGroup`).
- Nothing the backend failed on reaches the API or the UI: no exception text, provider or model
  names, status codes, model output or developer instructions. A known input problem raises
  `UserFacingError` with a message from `FailureMessage`; everything else gets the generic one.
  Event data carries no `problems` or raw text.

**API / live**
- Unknown session id → 404; actions in the wrong state → 409.
- Path traversal in any user-supplied path or filename.
- Out-of-order or duplicate live chunks; client disconnects mid-stream.

**Frontend**
- Model-written markdown is rendered without raw HTML; SVG figures load as images, not inline.
- Invalid LaTeX shows an error in place, not a broken page.
- Progress stream errors, reconnects and unknown event types are handled; loading, empty and error
  states are shown.
- After an API change, `schema.d.ts` is regenerated (`make gen-api`) and committed.

## 4. Tests

- Each behaviour above that applies has a test, including the failure paths.
- Tests are deterministic (no sleeps for timing where avoidable, seeded randomness) and fast.
- Tests assert on behaviour, not on implementation details that will churn.
- No real paid API calls; no network. Model responses are faked.
- Frontend logic (event handling, notes transforms) has `npm test` coverage.

## 5. Quality

- Matches the conventions and engineering practices in `CLAUDE.md`.
- Simple: no options, layers or abstractions without a current use; nothing more complex than the
  task needs.
- Reuse: nothing rebuilt that Iris or a dependency already provides (a component, CSS class or
  token, helper, or library); no near-copies of existing code.
- Scope: no unrelated refactors, renames or reformatting in the diff.
- One source of truth: no copied constants, settings, file names or user-facing wording.
- Logic that decides something is in pure functions with tests; routes, components and the
  Electron main process stay thin.
- Names and docstrings explain *why* where it isn't obvious; no dead code or stale TODOs for work
  this change completed. Module docstrings (Reads/Writes) and the docs match the behaviour.
- Tunable numbers are named constants or `Settings` fields with documented defaults, not magic
  numbers. New `Settings` fields are listed in `backend/.env.example`.
- Cost: new model calls are bounded, cached so re-runs don't re-pay, and use a model from
  `Settings`.
- Prompts treat session content (transcript, screen text, images) as data, never instructions.
- No new dependencies without a stated reason; no system dependencies.
- Security: no secrets in code or logs; user input validated at the API boundary.
