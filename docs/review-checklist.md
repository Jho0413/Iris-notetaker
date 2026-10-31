# Review checklist

Used by authors before asking for review and by the `code-reviewer` agent. Not every item applies to
every change; the reviewer says which sections it checked.

## 1. Correctness

- Does the code do what the task and `docs/architecture.md` say, including the stage contract
  (reads its inputs, writes only its own output file)?
- Off-by-one and unit errors: seconds vs milliseconds, frame index vs timestamp, inclusive vs
  exclusive ranges, pixel coordinates vs downscaled coordinates.
- Async: no blocking I/O or CPU work on the event loop; tasks are awaited or tracked; exceptions in
  concurrent work are not swallowed; cancellation leaves no half-written files.
- Resources: files, containers (`av.open`), and HTTP clients are closed (context managers).

## 2. Edge cases (all code)

- Empty input: zero frames, zero segments, zero moments, empty file, zero-length audio.
- Tiny input: one frame, one segment, audio shorter than one chunk.
- Large input: 90+ minute sessions. Memory must not scale with session length where avoidable
  (stream frames, don't load all into RAM).
- Malformed input: corrupt or truncated media, unknown container, a JSON file from an older version.
- Boundaries: values exactly at thresholds; first and last element; timestamps at 0 and at the end.
- Re-runs: running a stage twice gives the same result and doesn't duplicate output; a stage re-run
  after a crash recovers (principle 4).
- Non-ASCII: titles, filenames and transcript text with emoji, accents, CJK.

## 3. Stage-specific edge cases

**Ingest**
- Audio-only file; video with no audio; multiple audio or video streams (first is used).
- Non-zero stream start time; variable frame rate; rotated phone video; odd dimensions.
- Very long file decoded faster than real time; frames sampled at the right timestamps.

**Detect**
- Cursor movement, blinking caret, video-call thumbnails, compression noise: no new moment.
- Slide builds, app switches, scroll-then-settle: handled as documented.
- Transitions/animations never chosen as the representative frame.
- Returning to an earlier slide; static screen for the whole session; constantly changing screen
  (a playing video) does not explode the moment count.

**Transcribe**
- Chunk cuts land in silence; no words lost or duplicated at chunk boundaries.
- Timestamps are session-absolute after merging; segments sorted.
- Silent audio; audio that is all speech (no silence to cut at); one chunk fails while others succeed.
- Cached chunks reused on re-run; cache invalidated when boundaries or model change.

**Select / Write**
- Model output validated: crops inside frame bounds, ids that exist, required fields present.
  Invalid output is rejected or repaired, never trusted.
- Never more visuals in the notes than were selected; no invented `[visual:id]` markers survive.
- Prompt inputs bounded (long sessions don't exceed context or the cost budget).

**API / live**
- Unknown session id → 404; actions in the wrong state → 409.
- Path traversal in any user-supplied path or filename.
- Out-of-order or duplicate live chunks; client disconnects mid-stream.

## 4. Tests

- Each behaviour above that applies has a test, including the failure paths.
- Tests are deterministic (no sleeps for timing where avoidable, seeded randomness) and fast.
- Tests assert on behaviour, not on implementation details that will churn.
- No real paid API calls; no network.

## 5. Quality

- Matches the conventions in `CLAUDE.md`.
- Names and docstrings explain *why* where it isn't obvious; no dead code or stale TODOs for work
  this change completed.
- Tunable numbers are named constants or params with documented defaults, not magic numbers.
- No new dependencies without a stated reason; no system dependencies.
- Security: no secrets in code or logs; user input validated at the API boundary.
