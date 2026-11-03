# Iris roadmap

Updated: 3 November 2026.

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
- Model cost is not tracked. About $2.50 was spent in one day of development runs; the figure
  checks, screen reading, notes and SVG calls on `gpt-5.5` are suspected to be the main driver,
  alongside image generation. This needs measuring.
- Long transcripts are processed in sections and joined; there is no final synthesis pass yet.
- Live ingestion and incremental processing are not implemented; ending a live session fails.
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

- [ ] Record every model call: stage, model, input/cached/output/reasoning tokens, image tokens,
      audio seconds and latency.
- [ ] Keep model prices in a configuration table checked against OpenAI's pricing page, and write
      per-stage and per-model totals to each session (`usage.json`, API and `iris cost`).
- [ ] Confirm the main cost drivers, starting with `gpt-5.5` (screen reading, notes, figure checks,
      SVG) and image generation.
- [ ] Add controls: model per stage (try cheaper models where quality holds, e.g. for figure checks),
      reasoning effort, image quality, a cap on generated figures per hour (screenshots beyond it)
      and render attempts.
- [ ] Measure cost and latency per hour of session for each recording type against the README's
      goals ($0.50–2 per hour; notes within a few minutes for a 1-hour upload).

### Validate the upload experience

- [ ] Build a small, varied set of real recordings: slides, a meeting with screen sharing, a
      software demo, code, a camera-filmed whiteboard, an architecture-diagram walkthrough and a
      representative 30–90 minute session. Re-run it after prompt or model changes.
- [ ] Review transcript accuracy, figure relevance and quality, note accuracy and placement.
- [ ] Check repeated content discussed in different contexts, later corrections, audio-only input,
      long static screens and rapidly changing demonstrations.
- [ ] Decide the fallback when no redraw passes its check (more attempts, the best redraw marked
      unverified, or the source crop), and retry transient API failures per figure.
- [ ] Open exported notes in the intended Markdown viewer and verify the embedded visuals.

### Design and complete the UI

- [ ] Document the screen flows and visual design in `docs/frontend.md`.
- [ ] Design and implement the library, upload, live session and finished-notes screens with
      consistent typography, spacing, navigation and readable visual placement.
- [ ] Make recording state, capture status and the stop action clear throughout a live session.
- [ ] Display transcript and draft updates without disruptive scrolling or layout shifts.
- [ ] Distinguish recording, processing, draft, finished and failed states.
- [ ] Show partial results as they arrive: transcript, rendered figures and draft notes.
- [ ] Show each session's model cost and time per stage.
- [ ] Provide useful empty/loading/error states and clear retry and export actions.
- [ ] Check keyboard access, focus, contrast and layouts at common laptop widths.
- [ ] Test the whole workflow in the browser, including permission denial and interrupted requests.

### Produce coherent finished notes

- [ ] Add a final synthesis pass for long and live sessions that consolidates repeated points and
      reconciles later corrections while preserving distinct explanations and useful visuals.
- [ ] Keep a worked example or process that spans writing sections in one piece, and deduplicate
      figures across the whole session, not only recent moments.
- [ ] Keep one visual style across a session's generated figures.
- [ ] Preserve evidence behind decisions, action items and uncertainty; avoid invented conclusions.
- [ ] Validate visual references after synthesis and retain recovery/caching behavior.

### Complete live capture and ingestion

- [ ] Finalize browser audio chunks into the standard audio format and build the frame index.
- [ ] Preserve timestamps and audio/screen alignment through recording and finalization.
- [ ] Handle microphone/screen permissions, capture cancellation and screen sharing ending.
- [ ] Handle failed uploads and out-of-order or repeated chunks without silently losing content.
- [ ] Flush pending audio and frames before ending a session, and retain captured data for retries.
- [ ] State which browsers and shared-audio modes are supported and validate them manually.

### Process sessions while they happen

- [ ] Extend the transcript as audio arrives.
- [ ] Detect and close visual moments incrementally, then align and select relevant visuals.
- [ ] Update a running draft with text and images during recording.
- [ ] Bound concurrent work and update frequency so processing keeps up without excessive costs.
- [ ] Finalize the last audio and visual moment when recording stops and produce finished notes.
- [ ] Recover from transient model/API failures using saved inputs and completed work.

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
3. Improve the frontend: write `docs/frontend.md`, then build the designed screens with partial
   results, clear states and per-session cost.
4. Live sessions: final synthesis pass, live ingestion, then processing while recording.
5. Run end-to-end real-session checks, fix the gaps and document the MVP's supported behavior.
