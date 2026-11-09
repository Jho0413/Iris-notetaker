# Iris

**Notes that see what was shown.**

Iris turns a session where someone shows things while talking (a lecture, a meeting, a demo) into
notes that keep what was on screen. The slide, the whiteboard sketch or the diagram being discussed
is redrawn as a clean figure, checked against the original, and placed next to what was said about
it. Upload a recording, or record live in the browser or the macOS app.

## Your data

Everything Iris makes stays on your machine. Recordings, frames, transcripts, figures, notes and
your edits are files on your disk; in the Mac app they all live in
`~/Library/Application Support/Iris`, with your OpenAI key kept there encrypted. There is no Iris
server, no account, no cloud storage, no analytics and no crash reporting.

The one thing that leaves your machine is what OpenAI needs to make the notes, sent with your own
key:

- the audio, to transcribe it;
- the screens worth reading (finding them happens on your machine), with what was said over them;
- each figure's crop from the screen, to redraw it and to check the redraw against it;
- the text of the notes, to write and polish them.

The Mac app also checks a new key with OpenAI once, when you enter it. OpenAI says it doesn't use
API data to train its models:
[how OpenAI handles API data](https://developers.openai.com/api/docs/guides/your-data).

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and Node 22.12 or later.

```sh
make setup                              # install backend, frontend and desktop app deps
cp backend/.env.example backend/.env    # add OPENAI_API_KEY
make dev                                # API on :8000, UI on http://localhost:5173
```

### The macOS app (Apple Silicon)

```sh
make setup && make desktop              # desktop/dist/Iris-<version>-arm64.dmg
```

Open the `.dmg`, drag Iris to Applications and open it. The first launch asks for your OpenAI key;
macOS asks for the microphone, System Audio Recording and Screen Recording the first time each is
used. No terminal or repo is needed to run it.

## Learn more

- [`docs/overview.md`](docs/overview.md): what Iris is made of and the principles behind it. Start
  here.
- [`docs/development.md`](docs/development.md): setting up, running, the `iris` CLI, checks and
  debugging.

## Layout

```
backend/    the engine (Python, FastAPI): API, pipeline, live processing
frontend/   the web UI (React, TypeScript)
desktop/    the macOS app (Electron): shows the UI and runs the engine
docs/       how it works
```

CI (`.github/workflows/ci.yml`) runs the backend, frontend and desktop checks on every pull request
and must pass before merging.
