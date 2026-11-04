# Iris roadmap

Updated: 4 November 2026.

The first milestone is a complete live MVP: someone can record a session, watch useful notes with
professional visuals appear, and leave with a finished, downloadable document in a well-designed UI.
Later milestones build on feedback from using that product. They describe direction rather than
fixed release dates; revisit their scope after the MVP is in use.

## Current state

- The upload pipeline is implemented: ingest, transcribe, detect, align, select, write, render and
  assemble.
- Detection records what is added within a moment (builds) and follows vertical canvas scrolling,
  so a worked example or a drawing built up in stages stays one moment with ordered steps.
- Selection reads screens into math (LaTeX), text, tables, code and figures. Notes follow the
  discussion: worked examples, processes and sequences are written step by step, with each stage's
  figure inside its step.
- Figures are rebuilt professionally (generated illustrations, SVG diagrams, or screenshots for
  clean or highly detailed figures) and checked against the source before use.
- The session view renders math with KaTeX, step/answer cards, callouts and captioned figures.
  Markdown export keeps LaTeX intact and embeds figures.
- Validated so far on two short tablet recordings (a worked algebra example and a sketched physics
  explanation), with prompts free of sample-specific examples. Slides, meetings, demos, code,
  camera-filmed whiteboards, long sessions and the screenshot path are untested.
- Every model call is recorded per session (`usage.json`, `GET /api/sessions/{id}/usage`,
  `iris cost`) with tokens, images, audio seconds, latency and cost from a price table. About
  $2.50 was spent in one day of development runs before tracking; the figure checks, screen
  reading, notes and SVG calls on `gpt-5.5` are suspected to be the main driver, alongside image
  generation. This now needs measuring on real sessions.
- Long transcripts are processed in sections and joined, then a final synthesis pass consolidates
  them (merges split worked examples, applies later corrections, drops repeats, deduplicates
  figures). Its quality on real long sessions is untested.
- Live capture records mic and screen from the browser, and live sessions are processed while
  they record: the transcript, moments, screen reading, figure redraws and the notes sections that
  are final are made during the session, and at stop the live work is kept (promoted), so only
  what was still open is new work. Only finished notes are shown (the user's decision): progress
  while recording and processing, the notes once done. First real measurements are below; long live sessions are untested.
- Library, upload, live and session screens are basic; the intended UI design is not done.

## Milestone 1 — Complete live MVP

**Outcome:** a useful local app for one person, supporting both uploaded recordings and live browser
capture. Live functionality and a well-designed UI are part of this milestone.

### Step-by-step notes and professional visuals (done)

- [x] Detect incremental changes (newly written content and its region) within a moment, not only
      whole-screen changes; follow vertical scrolling.
- [x] Group changes with the transcript into steps: what was written plus the spoken reasoning.
- [x] Write step-by-step notes for worked examples, processes and sequences (problem or
      introduction, steps with their figures, answer or result, key-idea and watch-out callouts).
- [x] Rebuild every figure professionally, choosing a renderer per figure: generated illustration,
      SVG diagram, or screenshot for clean or highly detailed figures; math is typeset LaTeX.
- [x] Verify each redraw against the source and brief; retry with feedback, then fall back.
- [x] Render the notes in the UI (KaTeX, step cards, callouts, figures) and export them.

### Cost tracking and controls (next)

- [x] Record every model call: stage, model, input/cached/output/reasoning tokens, image tokens,
      audio seconds and latency.
- [x] Keep model prices in a configuration table checked against OpenAI's pricing page, and write
      per-stage and per-model totals to each session (`usage.json`, API and `iris cost`).
- [ ] Confirm the main cost drivers, starting with `gpt-5.5` (screen reading, notes, figure checks,
      SVG) and image generation.
- [ ] Add controls: model per stage (try cheaper models where quality holds, e.g. for figure checks),
      reasoning effort, image quality, a cap on generated figures per hour (screenshots beyond it)
      and render attempts. Done so far: reasoning effort per call type
      (`IRIS_{SELECT,NOTES,DIAGRAM,CHECK}_REASONING_EFFORT`), alongside the existing image quality
      and render attempts settings. Still to do: a figure cap, and a separate figure-check model.
- [ ] Measure cost and latency per hour of session for each recording type against the README's
      goals ($0.50–2 per hour; notes within a few minutes for a 1-hour upload).

### Validate the upload experience

- [ ] Build a small, varied set of real recordings: slides, a meeting with screen sharing, a
      software demo, code, a camera-filmed whiteboard, an architecture-diagram walkthrough and a
      representative 30–90 minute session. Re-run it after prompt or model changes.
- [ ] Review transcript accuracy, figure relevance and quality, note accuracy and placement.
- [ ] Check repeated content discussed in different contexts, later corrections, audio-only input,
      long static screens and rapidly changing demonstrations.
- [x] Decide the fallback when no redraw passes its check (more attempts, the best redraw marked
      unverified, or the source crop), and retry transient API failures per figure. Decided: the
      source crop, marked unverified, with no extra attempts beyond `IRIS_RENDER_ATTEMPTS` per
      renderer. An API error while redrawing is not retried per figure: the figure uses its
      source crop at once (unverified, not cached, so a later run tries again), and the run
      carries on. The UI labels it "Original image", with no reason (architecture section 1,
      "Errors and retries").
- [ ] Open exported notes in the intended Markdown viewer and verify the embedded visuals.

### Design and complete the UI

- [ ] Document the screen flows and visual design in `docs/frontend.md`.
- [ ] Design and implement the library, upload, live session and finished-notes screens with
      consistent typography, spacing, navigation and readable visual placement.
- [ ] Make recording state, capture status and the stop action clear throughout a live session.
- [ ] Show progress while recording and processing without disruptive scrolling or layout shifts;
      only finished notes are shown (the user's decision: no live draft, no draft while processing
      and no assembled notes before polishing ends).
- [ ] Distinguish recording, processing, polishing, finished and failed states.
- [ ] Show progress as it happens: the current step with counts, every step and the run time
      while processing; one progress line while recording.
- [ ] Show each session's model cost and time per stage.
- [ ] Provide useful empty/loading/error states and clear retry and export actions.
- [ ] Check keyboard access, focus, contrast and layouts at common laptop widths.
- [ ] Test the whole workflow in the browser, including permission denial and interrupted requests.

### Produce coherent finished notes

- [x] Add a final synthesis pass for long and live sessions that consolidates repeated points and
      reconciles later corrections while preserving distinct explanations and useful visuals.
- [x] Keep a worked example or process that spans writing sections in one piece, and deduplicate
      figures across the whole session, not only recent moments.
- [ ] Keep one visual style across a session's generated figures.
- [ ] Preserve evidence behind decisions, action items and uncertainty; avoid invented conclusions.
      (Synthesis rejects rewrites that lose decision, action-item or uncertainty wording, math or
      figures; not yet measured on real sessions.)
- [x] Validate visual references after synthesis and retain recovery/caching behavior.

### Complete live capture and ingestion

- [ ] Finalize browser audio chunks into the standard audio format and build the frame index.
- [ ] Preserve timestamps and audio/screen alignment through recording and finalization.
- [ ] Handle microphone/screen permissions, capture cancellation and screen sharing ending.
- [ ] Handle failed uploads and out-of-order or repeated chunks without silently losing content.
- [ ] Flush pending audio and frames before ending a session, and retain captured data for retries.
- [ ] State which browsers and shared-audio modes are supported and validate them manually.

### Process sessions while they happen

- [x] Extend the transcript as audio arrives.
- [x] Detect and close visual moments incrementally, then align and select relevant visuals.
- [x] Write the final notes sections during recording, so stop only writes what was still open.
      (A running draft was built first; per the user's decision only finished notes are shown,
      so no draft is written or shown: progress while recording and processing instead.)
- [x] Bound concurrent work and update frequency so processing keeps up without excessive costs.
      (Concurrency settings; on the two real recordings live work cost about the same
      as an upload. Not yet measured on long sessions.)
- [x] Finalize the last audio and visual moment when recording stops and produce finished notes.
      The live transcript, moments, alignment and selection are promoted at stop; the run
      continues from write (or from detect if only the transcript is usable).
- [x] Recover from transient model/API failures using saved inputs and completed work. Errors
      are classified (temporary, blocking, rejected, bad output, internal). Each unit (a
      transcription chunk or live window, a selection batch, a notes section, a synthesis call)
      is retried after a temporary error (`IRIS_API_RETRY_DELAYS_S`, after the SDK's own
      retries); a figure falls back to its source crop; a blocking error (key, credit, model)
      fails the run at once and cancels the work in progress. Finished units are cached, so
      Try again resumes from the failed step. The user sees only our own messages; the raw
      error is in the log and `iris show` (architecture section 1, "Errors and retries").

#### First real live measurements (4 November 2026)

Each recording was replayed into the live API in real time with `iris replay-live` (audio as
~5 s WebM chunks, frames once a second) on a scratch backend, then uploaded to the same backend.
Default settings except the window lengths noted. Times are seconds of session time from the
start of recording; costs are from `/usage`. Measured with the running draft, since removed: the
"First draft" and "Live draft refreshes" rows no longer apply (the draft lane now writes only the
sections that are final, so these one-moment recordings write none live).

| | 22446 (78 s, worked algebra) | 22446 again, windows 5–15 s | 22450 (53 s, sketched physics) |
|---|---|---|---|
| First transcript | 37.8 s | 21.6 s | 36.9 s |
| First draft | 45.6 s | 26.5 s | 40.6 s |
| First moment / figure | 78.1 s / none (math only) | 78.1 s / none | 53.4 s / 73.6 s (both at stop) |
| Live draft refreshes | 1 | 1 | 1 |
| Promoted at stop | transcribe, detect, align, select | same | same |
| Stop to done | 25.4 s (select of the one moment 15.9 s, write 6.9 s) | 30.9 s (select 23.4 s) | 241.6 s (select 17.6 s, render of 3 figures 221 s) |
| Cost: total / after stop | $0.114 / $0.084 (6 calls) | $0.121 / $0.098 (9 calls) | $0.892 / $0.874 (15 calls) |
| Upload of the same file | 25.4 s, $0.087 (3 calls) | | 188 s then failed on a transient render API error; resumed from render: $1.155 in all |

- **Notes:** the same as the uploads and `sample-notes/`: the algebra notes have the same three
  steps, callouts and answer $3(x+3)^2-28$; the physics notes have the same four steps with three
  figures placed in steps 1, 2 and 4. No regressions found. One live figure (the orbiting black
  holes) is an SVG fallback after the image redraw failed its check, plainer than the upload's
  image; that is render variance, not a live effect.
- **Both recordings are one moment each** (one continuous worked example or sketch), which only
  closes at stop. So nothing could be read or redrawn while recording, and stop-to-done is about
  an upload's processing time minus transcription. Promotion still saved re-transcribing and
  re-reading. Sessions with several screens should finish much faster after stop; a long session
  with screen changes is the next thing to measure.
- **Transcript latency** was about 37 s with 10–30 s windows: a cut is decided once the maximum
  window plus 1 s is decoded, and audio arrives in 5 s chunks. With 5–15 s windows (now the
  default) the first transcript and draft came about 16–19 s sooner, the transcript text was the
  same, and whisper cost $0.0081 instead of $0.0079 (billing rounds up per window).

### Document and verify the MVP

- [ ] Update setup instructions, supported inputs, capture limitations and recovery guidance.
- [ ] Complete the architecture sections needed to explain the implemented pipeline and live flow.
- [ ] Add automated coverage for live ingestion, incremental processing and final synthesis.
- [ ] Record a small set of real examples and their quality, timing and cost results.

**Complete when:** a user can upload or record a real session, see notes with visuals updating during
live capture, stop, read coherent finished notes, and export them through the designed UI. The
representative real sessions have been reviewed, known limitations are documented, and recoverable
failures have a usable retry path. A passing mocked pipeline test alone does not complete the MVP.

**Scope boundary:** local, single-user browser app. Desktop packaging, meeting bots, accounts,
collaboration and in-app note editing belong to later milestones.

## Milestone 2 — Reliable daily use

**Outcome:** Iris becomes dependable for repeated use, with better ways to find, verify and refine
past notes. Basic recovery and usable design already exist in milestone 1; this milestone expands
coverage and improves the product using real usage.

- [ ] Build a broader evaluation set and track regressions in transcription, visual selection,
      grounding, note completeness and visual placement.
- [ ] Improve recovery across app/backend restarts, long interruptions and partially completed jobs.
- [ ] Tune latency, memory, storage and model cost using measured workloads.
- [ ] Add searchable sessions and clearer library organization.
- [ ] Add note editing while preserving visual references and the original generated result.
- [ ] Make it easy to inspect the transcript and source moment behind a note or image.
- [ ] Establish repeatable release checks and data backup/deletion guidance.

**Complete when:** the evaluation set has agreed quality and performance thresholds, recovery checks
pass, and users can repeatedly capture, find, inspect and edit notes without routine manual repair.
Set those thresholds from milestone 1 measurements and usage feedback.

## Milestone 3 — Desktop app

**Outcome:** users can install and run Iris without managing separate development processes.

- [ ] Package the UI and backend in a desktop application.
- [ ] Add native screen and system-audio capture through the existing source interface.
- [ ] Support background recording with clear recording controls and indicators.
- [ ] Provide setup, permissions, secure credential storage and actionable diagnostics.
- [ ] Add application updates and safe migration of existing local sessions.
- [ ] Choose the first supported operating system and validate installation, capture and upgrades.

**Complete when:** a fresh install can capture, process and export a session, recover saved work after
restart, and update without losing sessions on the supported operating system.

## Milestone 4 — Meeting automation

**Outcome:** Iris fits into the user's meeting workflow with less manual capture and export work.

- [ ] Choose an initial meeting platform and implement its capture or bot integration.
- [ ] Preserve timestamped audio and visual inputs so the same pipeline can process them.
- [ ] Add explicit controls for starting, stopping and scheduling automatic capture.
- [ ] Make recording visibility, participant consent requirements and platform limitations clear.
- [ ] Integrate delivery to a selected notes/document tool, with user-controlled destinations.
- [ ] Handle authentication expiry, disconnects and retries without duplicate sessions or deliveries.

**Complete when:** an authorized meeting can be captured, processed and delivered through one
supported integration with clear status and failure recovery. Expand platforms based on demand.

## Later possibilities

Shared workspaces, team accounts and collaboration are possible future directions, not committed
milestones. Decide whether to pursue them from actual usage and demand; they would introduce new
hosting, access-control and data-handling requirements.

## Immediate sequence within milestone 1

1. ~~Merge the pipeline work (`feat/pipeline`) into `main`.~~ Done (PR #1), with CI and the review
   process (PRs #2, #3).
2. Add cost tracking and controls; confirm where the cost goes and bring sessions within budget.
   Collect the varied recordings alongside, so costs are measured on realistic sessions.
3. Improve the frontend: write `docs/frontend.md`, then build the designed screens with progress
   until the notes are finished, clear states and per-session cost.
4. Live sessions: final synthesis pass, live ingestion, then processing while recording.
   Processing while recording is designed in architecture section 1 ("Life of a session: live")
   and built on `feat/live-processing`: the foundation (timeline, processor, lane contracts)
   first, then these branches from it, in parallel, each merged back by PR:
   - `feat/live-audio`: the audio lane (decode as chunks arrive, cut at pauses, transcribe).
   - `feat/live-screen`: the screen lane and the select lane (moments, screen reading, renders).
   - `feat/live-draft`: the draft lane, `GET /sessions/{id}/notes/draft` and the live UI (the
     draft and its endpoint were later removed on `feat/final-notes-only`: only finished notes
     are shown).
   - `feat/synthesis`: the final synthesis pass (also for long uploads).
   Then `feat/live-finish`: promote the live outputs at stop, end-to-end checks on real
   recordings (built; first measurements above). `feat/live-processing` merges to `main` once
   that is done.
5. Run end-to-end real-session checks, fix the gaps and document the MVP's supported behavior.
