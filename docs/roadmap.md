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
  clear failure messages with retry, PDF export (Markdown from the API), and editing the notes in place
  (boxes, numbered steps and figures in an explicit notes format; edits kept apart from the
  generated notes and through re-runs).
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

- [x] Prototype: capture the screen plus system audio on macOS (decides whether a small native
      helper is needed alongside Electron). No helper needed: Electron (`desktop/`) records any
      screen or window from the macOS system picker with system audio, through the UI's own
      capture code. Two live recordings worked end to end. Frames stay at 1 fps with Iris in the
      background only because the app turns off Chromium's background throttling.
- [x] Package the backend into the app (no `uv` or repo needed): a standalone Python with Iris
      installed (`make desktop`). `Iris.app` is 581 MB (Python and Iris 327 MB, Electron 243 MB),
      the `.dmg` 224 MB. A warm launch shows the UI in about 2.6 s. PyAV and OpenCV each bundle
      their own FFmpeg (harmless today, ~17 MB unused). Ad-hoc signed, not notarized.
- [x] Electron app wrapping the current UI and starting the backend itself, with an API key
      screen: the UI over `app://iris`, `/api` forwarded with a per-launch token, the backend
      stopping with the app (also when it crashes), one instance at a time.
- [ ] Sign and notarize with a Developer ID, then measure the first launch of a downloaded copy
      (about 40 s unsigned, almost all macOS checking each native library once).
- [ ] Record any screen or window, stop, and save the `.md` with its images to a chosen folder.
- [ ] Windows support.

**Done when:** the engine has been reviewed and measured on the varied recordings, and the desktop
app can record anything on screen and save finished notes on macOS and Windows.

## Later: integrations

Feed Iris from other software and send the notes on, adding to the input or output format only
when an integration needs it. Candidates: meeting platforms (Zoom, Teams, Meet) and delivery to
note tools (Obsidian, Notion, Google Docs). Choose from real use.
