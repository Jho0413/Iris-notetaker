# Iris roadmap

Updated: 4 November 2026.

Iris is a notes engine: the backend turns a recording (audio plus screen) into finished Markdown
notes with professional visuals. Anything can feed it (the web app today, a desktop app and other
integrations next), and the receiver renders the `.md` however it wants. Input and output formats
stay as they are until an integration needs more.

## Done: MVP

- **Pipeline:** ingest, transcribe, detect, align, select, write, render, assemble and synthesize.
  Worked examples and processes are written step by step, math is LaTeX, and figures are redrawn
  and checked against the source (the source crop is used if no redraw passes).
- **Live:** record mic and screen in the browser; the session is processed while it records, so
  stop only does what was still open.
- **Web app:** library, upload, live and finished-notes screens with progress, cost per session,
  clear failure messages with retry, and Markdown export.
- **Cost tracking:** every model call is recorded per session (`usage.json`, API, `iris cost`),
  with reasoning effort configurable per call type.
- **Errors:** temporary errors are retried, blocking ones fail at once, and Try again resumes from
  the failed step.
- **Checked on:** two short tablet recordings, live and as uploads, with the same notes either
  way: worked algebra (78 s, $0.11, done 25 s after stop) and sketched physics with 3 figures
  (53 s, $0.89, done 242 s after stop, 221 s of it redrawing figures). Each was one continuous
  screen, so its reading and redraws all happened at stop. A live session with a real mic and
  screen share also works in Chrome.

## Next: test the engine and build the desktop app

Two tracks that can run side by side.

### Test the engine

- [ ] Collect a small, varied set of real recordings: slides, a meeting with screen sharing, a
      software or code demo, and a 30–90 minute session.
- [ ] Run each as an upload and live; review the notes for accuracy, figure quality and placement.
- [ ] Measure cost and time per hour against the goals ($0.50–2 per hour; notes within a few
      minutes for a 1-hour upload).
- [ ] Fix what the measurements show, e.g. a cap on redrawn figures per hour or a cheaper
      figure-check model.

### Desktop app

- [ ] Prototype: capture the screen plus system audio on macOS (decides whether a small native
      helper is needed alongside Electron).
- [ ] Electron app wrapping the current UI and starting the backend itself.
- [ ] Record any screen or window, stop, and save the `.md` with its images to a chosen folder.
- [ ] Windows support.

**Done when:** the engine has been reviewed and measured on the varied recordings, and the desktop
app can record anything on screen and save finished notes on macOS and Windows.

## Later: integrations

Feed Iris from other software and send the notes on, adding to the input or output format only
when an integration needs it. Candidates: meeting platforms (Zoom, Teams, Meet) and delivery to
note tools (Obsidian, Notion, Google Docs). Choose from real use.
