# Pipeline

How a session becomes notes: the life of an upload, then the stages with notable behaviour. Ingest,
transcribe, align and assemble are described in their module docstrings. Live sessions run the same
stages while recording ([`live.md`](live.md)); failures follow [`errors.md`](errors.md).

## Life of a session: upload

1. **Create.** The UI uploads a file. The backend saves it as `sessions/<id>/source.<ext>` and starts a
   job.
2. **Ingest** (seconds, not real time). PyAV extracts 16 kHz mono audio and samples frames at a
   low fixed rate (`IRIS_FRAME_FPS`, default 1 fps), downscaled, each tagged with its timestamp.
3. **Transcribe and detect, in parallel.**
   - *Transcribe:* audio is split at silences into chunks, transcribed concurrently, and merged into
     one timestamped transcript. Every chunk is sent the spoken language, chosen on the Settings
     page or pinned by `IRIS_TRANSCRIBE_LANGUAGE` (default `en`; `auto` lets the model detect it,
     which can misread accents, e.g. British English as Welsh). The notes are written in the transcript's language.
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
7. **Assemble.** Markers are resolved to figures by id (`::figure{#<id>}`). The notes are stored
   exportable as Markdown with figures embedded (`GET /export`, for other clients); the app
   exports them as a PDF.
8. **Synthesize.** A pass across the whole session consolidates the assembled notes (a worked
   example split across sections, a later correction, a slide returned to, a figure picked twice),
   and the polished notes replace them. If it fails, the assembled notes are the result and the
   session still finishes.

The UI receives progress events throughout (server-sent events): transcript chunks as they finish,
moments as they are found, figures as they are rendered, and notes sections as they are written.
It shows only progress until the session is done, then the finished notes: no draft or assembled
notes are shown before then (the user's decision).

## Stage order

Order (`pipeline.PLAN`): ingest → transcribe ∥ detect → align → select → write ∥ render → assemble →
synthesize. Each stage appears once, so re-running from a stage (`plan_from`), the session's
`current_stage` and the UI's progress list need no special cases. A run that includes select
deletes `notes_draft.json` first: visual ids may change, so a draft an earlier run wrote against
the old selection is never assembled (say write fails and a re-run starts from assemble). A run
from write keeps it; write rewrites it.

## Detect

Each moment records its *builds* (content added over time, such as lines of handwritten working) and
follows canvas scrolling, so a worked example or a sketch drawn on a scrolling page stays one
moment. A live recording's screen shares (`FrameIndex.share_starts`) are compared only within
themselves, and a moment ends where its share does. Details are in the `detect.py` docstring.

## Select (read the screen)

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

## Write

- `IRIS_NOTES_MODEL` writes meeting, lecture or demo notes from what was said and what was on screen.
- Worked examples are written step by step: problem, steps with typeset working and reasoning,
  answer, then key ideas and watch-outs. All math is LaTeX (`$...$`, `$$...$$`). Each figure is
  placed once, beside the explanation it illustrates; a step's figure goes inside the step.
- **The notes format** (`iris/notes_format.py`; the UI's copy is `frontend/src/notes/format.ts`):
  Markdown plus three commands. `:::box[Title]{color=…}` … `:::` is a box and `:::step[Title]{color=…}`
  … `:::` a step on a numbered path (steps in a row form one path; the number is drawn, never
  written); `::figure{#<id>}` places a figure of the session by id. The word after the colons is a
  command and is never shown; the reader sees only the writer's own title. Each closing `:::`
  closes the most recent block still open, blocks go one level deep (a box in a step or box), and
  colours come from a palette of ten names (grey when missing or unknown). Nothing in the notes is
  matched by its words. The writer is asked for Iris's convention: an indigo Problem box and indigo
  steps, a green Answer or Decision, blue Definition and Key idea, amber Watch out and Open
  question, grey for the rest, titles in the notes' language.
- **Model output is checked:** a section whose blocks use an unknown command, go too deep or place
  figures by command is invalid (not cached; Try again rewrites it). What needs no guessing is
  repaired instead: an unknown colour becomes grey, a block left open is closed at the end of its
  section, a `:::` that closes nothing is dropped. A colour name never fails a session.
- **Old notes** (`Notes.format_version` 1, from before the format, with bold "Problem:", "Step n:"
  and "Answer:" labels and image links) are upgraded in memory whenever they are read (the API and
  export), grouped exactly as the old reading view drew them (`upgrade_legacy`); files on disk are
  never rewritten.
- All transcript text is used. Long transcripts are split into chronological sections of up to
  `IRIS_NOTES_CONTEXT_CHARS` evidence characters per call (default 24,000, range 2,000–60,000).
  Oversized speech segments are split without dropping text or inventing timestamps.
- Sections keep the first section's title and style and are joined in order. Consolidating them
  across the session is the synthesize stage's job.
- Validated sections are cached in `notes_sections/`; damaged entries are regenerated. The draft is
  saved, and a progress event emitted, after each section. Notes are available after assembly;
  the UI shows them once the session is done, never the draft.

## Render

Runs alongside note writing and rebuilds every figure professionally.

- `IRIS_DIAGRAM_MODEL` draws exact figures (graphs, charts, schematics) as SVG; `IRIS_IMAGE_MODEL`
  draws realistic or illustrative figures from the brief. Figures that are already clean, or too
  detailed to redraw reliably (architecture diagrams, detailed maps, dense charts), stay as sharp
  screenshots.
- `IRIS_VISION_MODEL` checks each result against the source crop and brief. Failures are retried
  with the problems as feedback (`IRIS_RENDER_ATTEMPTS` per renderer), then the other renderer is
  tried. If nothing passes, the source crop is used and recorded in `renders.json` as unverified,
  so the notes never show a redraw that failed its check. An API error while redrawing gives the
  source crop at once, uncached; a blocking error fails the stage and cancels the figures still
  rendering ([`errors.md`](errors.md)). The notes record how each figure was made
  (`Notes.visuals[].verified`).
- Up to `IRIS_RENDER_CONCURRENCY` figures render at once. Model-written SVG is sanitized before it
  is stored. Completed renders are cached in `render_cache/`.
- Exports embed SVG figures as PNG, since Markdown viewers reject SVG data URIs. Boxes and steps
  keep their `:::` lines in the export (other Markdown apps show them as text).

## Synthesize

Notes are written forwards in sections (live, and for long uploads) and never look back. The
synthesis pass consolidates them across the whole session: it merges a split worked example,
applies a later correction, drops a repeated point and points duplicate figures at one.

Finishing is two steps: assemble publishes the notes as written (the result if synthesis fails;
meanwhile the UI shows *Polishing notes* and no notes), then synthesize consolidates them and the
pipeline runs assemble again inside the synthesize stage, so the polished notes replace the
assembled ones. Assemble uses `notes_synthesis.json` only while it was made from exactly the
current `notes_draft.json` and selection, so a re-run from an earlier stage never shows a stale
synthesis. A re-run from `synthesize` re-polishes without rewriting anything else.

- **Sections** are the notes split at their top-level (`#`, `##`) headings, outside code and
  outside boxes and steps (a heading inside a box never splits it). Writing
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
  fewer decision, action-item, open-question or uncertainty words and task items, any math it
  drops still appears elsewhere, its boxes and steps open and close within each section with no
  problem (unknown command, too deep, figure commands), and every box or step it had (by command,
  title and colour) is still somewhere: a merge may join or move them, never lose one. A rejected group keeps its sections as written; the others are
  applied. Across groups, every equation in the notes must survive, and every marker must resolve
  once to a selected visual.
- **Cost:** write records how many parts it planned (`NotesDraft.parts`). Notes written in one part
  make no call, since the writer saw everything at once, unless the sweep found look-alike figures;
  then one planning call may only drop duplicates, applied locally. Notes in several parts cost one
  cheap planning call, which may return no edits, plus one rewrite per group of touched sections.
  A draft that doesn't record its parts (an older version) counts as one part only if it has one
  section. Notes over 200,000 characters are left as assembled. Plans and rewrites are cached in
  `synthesis_cache/`, keyed by model, prompt, schema and inputs; a damaged entry is regenerated and
  an invalid result is never stored.
- **Safety:** each call is retried after a temporary API error ([`errors.md`](errors.md)).
  Any failure (API error, refusal, invalid output, missing key) is logged and recorded in
  `synthesis.json` (`status: failed`, with the reason); the session still ends `done` with the
  assembled notes. `synthesis.json` also records the repeats found, the edits applied and the
  sections rewritten or rejected. `IRIS_SYNTHESIS=false` turns the pass off.

## Edited notes

The user's edits never change the generated notes. The latest save is kept beside them in
`notes_edits.json` (`NotesEdits`: the markdown, `based_on` and `saved_at`), written by the API only
(`PUT /api/sessions/{id}/notes/edits`, checked with `notes_format.validate`: 422 names the first
problem in plain words, 409 while the session is processing), so a re-run, whose stages write
only their own files, keeps them by construction. `GET /notes` returns the edited version while
one exists, with `edited: {saved_at, stale}`, and `generated_hash`, which names the generated
notes the response is based on. An edit is `stale` once a re-run made different notes than it
started from (`based_on` ≠ `generated_hash`); the UI then lets the user keep the edits (saved again
on the new notes) or use the new notes (`DELETE …/notes/edits`, which is also "Restore generated
notes"). Export uses the edited notes when there are any.
