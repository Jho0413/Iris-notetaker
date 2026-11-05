# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person running Iris locally on their own laptop. Iris is for anyone who sits through a session
where something is shown while someone talks: students in lectures and tutorials, and
professionals in meetings, demos and screen-shared calls. Both audiences matter equally, so no
single session style may dominate the interface.

For live sessions, the user starts recording and gets on with the session. They rarely watch Iris
while it runs. What matters is that it is obviously still recording, that the stop action is easy
to find, and that useful notes are waiting afterwards.

## Product Purpose

Iris turns a recording, uploaded or captured live in the browser, into notes that include what was
on screen. The slide, whiteboard sketch or diagram is rebuilt as a clean visual and placed next to
what was said about it. Success means the user finishes a session holding coherent, readable notes
with accurate visuals, and can export them.

## Positioning

Audio-only notetakers lose what was shown. Iris keeps it: every visual is redrawn professionally
and checked against the source frame before it reaches the notes. If no redraw passes the check,
the original crop is shown and marked unverified.

## Operating Context

- Runs on a laptop: a FastAPI backend on :8000 and the web UI on :5173. There are no accounts and
  no hosting.
- Sessions last 30–90 minutes. A one-hour upload should produce notes within a few minutes, with
  progress shown until they're finished; only finished notes are shown. Processing costs $0.50–2 per hour of session.
- Notes are read and edited in the app, and exported from it as a PDF. The engine's API also
  gives them as Markdown with the figures embedded, for other apps.

## Capabilities and Constraints

- Session states: created, recording, processing, done, failed. The pipeline stages are ingest,
  transcribe, detect, align, select, write, render and assemble. These are internal names: the UI
  speaks in plain language and keeps the stage names in an on-demand details view.
- Progress arrives as server-sent events. Event history is kept in memory, so it is lost when the
  backend restarts.
- Notes contain KaTeX math, boxes with the writer's own titles in ten colours, numbered steps and
  captioned figures (the notes format, `backend/src/iris/notes_format.py`).
- Not in v1: accounts, sharing, a desktop app or a meeting bot.
- Per-session cost depends on the cost-tracking API (in progress). Live incremental processing is
  in progress too.

## Brand Commitments

- The name is Iris; the tagline is "Notes that see what was shown."
- The user chose a clean, neutral look in a system sans with a single accent colour, plus a simple,
  modern dark mode.

## Evidence on Hand

- Real sessions recorded during development are in `backend/data/sessions/` (worked algebra and
  sketched physics). There are no testimonials, metrics or customers, and none may be invented.

## Product Principles

1. Notes first: once a session is finished, the reading experience is the product.
2. Plain language: show what is happening in the user's terms, with technical detail on demand.
3. Never lose a session: every failure has a visible, working way to retry or resume.
4. Visuals are trustworthy: show whether a figure was verified or is the original crop.
5. Calm while recording: recording state and the stop action are unmistakable, and nothing else
   competes with them.

## Accessibility & Inclusion

Keyboard access for every action, visible focus, WCAG AA contrast in both themes, colour never the
only signal of state, and respect for reduced-motion preferences.
