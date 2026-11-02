# Iris roadmap

Updated: 1 November 2026.

The first milestone is a complete live MVP: someone can record a session, watch useful notes with
professional visuals appear, and leave with a finished, downloadable document in a well-designed UI.
Later milestones build on feedback from using that product. They describe direction rather than
fixed release dates; revisit their scope after the MVP is in use.

## Current state

- The upload pipeline is implemented: ingest, transcribe, detect, align, select, write and assemble.
- Selection and writing cache completed model responses and recover from invalid cache entries.
- Notes support meeting, lecture and demo styles, with selected frames or crops placed in the text.
- Markdown export embeds local images and handles punctuation in captions.
- Long transcripts are processed in sections and joined chronologically; there is no final
  whole-session synthesis pass yet.
- Basic library, upload, live capture and session screens exist. Live ingestion, incremental
  processing, partial-draft display and the intended UI design remain unfinished.
- Automated tests exercise the upload-to-export flow with mocked model calls. Real-recording
  accuracy, visual selection quality, latency and cost still need validation.

## Milestone 1 — Complete live MVP

**Outcome:** a useful local app for one person, supporting both uploaded recordings and live browser
capture. Live functionality and a well-designed UI are part of this milestone.

### Validate the upload experience

- [ ] Run real recordings through the complete pipeline with real model calls.
- [ ] Cover a meeting, lecture and demo, including a representative 30–90 minute session.
- [ ] Review transcript accuracy, visual relevance, crop readability, note accuracy and placement.
- [ ] Check repeated images discussed in different contexts, later corrections, audio-only input,
      long static screens and rapidly changing demonstrations.
- [ ] Measure stage latency and model cost; address issues against the README's performance and
      cost goals. Record measurements rather than treating those goals as already achieved.
- [ ] Open exported notes in the intended Markdown viewer and verify the embedded visuals.

### Step-by-step notes and professional visuals

- [ ] Detect incremental changes (newly written content and its region) within a moment, not only
      whole-screen changes.
- [ ] Group changes with the transcript into steps: what was written plus the spoken reasoning.
- [ ] Write structured notes (problem, steps, figures, answer, pitfalls) for worked examples, keeping
      the current format for slides and meetings.
- [ ] Rebuild every visual professionally, choosing a renderer per visual: LaTeX, plots, diagrams
      (Mermaid/SVG), tables, image generation or a cleaned screenshot.
- [ ] Verify each redraw against the source frames and transcript; regenerate on disagreement.
- [ ] Render the structured notes in the UI (KaTeX, step cards, figures) and export them.

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

### Produce coherent finished notes

- [ ] Add a final synthesis pass for long and live sessions that consolidates repeated points and
      reconciles later corrections while preserving distinct explanations and useful visuals.
- [ ] Preserve evidence behind decisions, action items and uncertainty; avoid invented conclusions.
- [ ] Validate visual references after synthesis and retain recovery/caching behavior.

### Design and complete the UI

- [ ] Document the screen flows and visual design in `docs/frontend.md`.
- [ ] Design and implement the library, upload, live session and finished-notes screens with
      consistent typography, spacing, navigation and readable visual placement.
- [ ] Make recording state, capture status and the stop action clear throughout a live session.
- [ ] Display transcript and draft updates without disruptive scrolling or layout shifts.
- [ ] Distinguish recording, processing, draft, finished and failed states.
- [ ] Provide useful empty/loading/error states and clear retry and export actions.
- [ ] Check keyboard access, focus, contrast and layouts at common laptop widths.
- [ ] Test the whole workflow in the browser, including permission denial and interrupted requests.

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

1. Validate a real upload and establish the initial quality, latency and cost baseline.
2. Build step-by-step notes and professional visuals; this sets the notes format the UI depends on.
3. Define the UI flows and visual design alongside the remaining live workflow.
4. Complete live ingestion and incremental processing.
5. Add final synthesis and connect the live transcript/draft to the designed UI.
6. Run end-to-end real-session checks, fix the gaps and document the MVP's supported behavior.
