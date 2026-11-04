# Development

Setup, everyday commands, cost tracking and troubleshooting. How we work (conventions, commits,
definition of done) is in [`../CLAUDE.md`](../CLAUDE.md).

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node 22+. Nothing else to install system-wide:
PyAV ships with FFmpeg built in.

```sh
make setup                        # install backend + frontend deps
cp backend/.env.example backend/.env   # add OPENAI_API_KEY
make dev                          # API on :8000, UI on http://localhost:5173
```

Settings are `IRIS_*` environment variables (`backend/src/iris/config.py`, documented in
`backend/.env.example`). Session data lives in `backend/data/` (`iris.db` plus `sessions/<id>/`).

## Commands

```sh
make test                         # backend tests, frontend tests and typecheck
make lint                         # ruff + frontend lint (CI also runs build)
make gen-api                      # regenerate the UI's typed client after API changes
make usage [<session_id>]         # model usage and cost (most recent session by default)
```

Running the pipeline without the UI:

```sh
cd backend
uv run iris process path/to/recording.mp4   # new session, full pipeline
uv run iris run <session_id> --from select  # re-run from a stage
uv run iris stage <session_id> detect       # run one stage on its own
uv run iris cost [<session_id>]             # model calls and cost per stage, model and purpose
uv run iris show <session_id>               # status, and for a failed run what it failed on
uv run iris replay-live <file>              # play a recording into the live API (architecture/live.md)
```

## Cost tracking

Every model call is recorded in the session's `usage.json` (also `GET /api/sessions/{id}/usage`):
tokens, images, audio seconds, latency and cost from the price table in `config.py`. Calls to
models missing from the table are reported as unpriced, not guessed. Cached work costs nothing.
The UI shows each session's cost in the library and the session rail, per step in its Details.

## Troubleshooting

When a run fails, the UI shows only a short message of our own ("Iris couldn't finish making the
notes. Try again; finished steps are kept.", or what to fix about the recording) and the step it
stopped at; it never shows the error itself. To see what went wrong:

- **`uv run iris show <session_id>`** (in `backend/`) prints the session's title, source, status,
  the stage it stopped at, the failure kind, the message the user saw, and the raw error with its
  time. The session id is in the page's URL (`/sessions/<id>`).
- **The backend log** (the terminal running `make dev` or `iris serve`) has the full traceback,
  every retry of a temporary API error, and each figure that fell back to its source crop.
- **`uv run iris cost <session_id>`** counts failed model calls; each one's error is in the
  session's `usage.json`.

Temporary API errors (timeouts, rate limits, server errors) are retried per unit of work after the
SDK's own retries, waiting `IRIS_API_RETRY_DELAYS_S` (default `[15, 60, 120]` seconds). A missing
or rejected `OPENAI_API_KEY`, exhausted credit or a missing model fails the run at once; fix it and
**Try again**. The whole policy is in [`architecture/errors.md`](architecture/errors.md).
