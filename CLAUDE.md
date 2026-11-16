# Iris: working agreement

Read `README.md` and `docs/overview.md` first (the other pages in `docs/` cover the engine, making
notes, recording live, the UI and the desktop app). This file covers how we work.

## How we build a change

A **bigger change** (a feature, a new screen or flow, an API change, anything with a design or
behaviour choice in it) goes through these steps, in order:

1. **Plan and brainstorm.** Read the issue and the code it touches, then work out the approach
   with the user: lay out the options and their trade-offs, and recommend one. Write a short
   plan: what changes, where, and what's out of scope. Specify the behaviour (see "Specifying
   behaviour" below) and agree it with the user. For UI work, show 2–3 options as a clickable
   HTML mockup (using the real `frontend/src/index.css`, served on a local port) and let the user
   pick.
2. **Ask.** Put the decisions that are the user's to make (scope, behaviour, wording) to them as
   clarifying questions instead of guessing. Build only once the plan is agreed.
3. **Implement** on the task's own branch, following the conventions below.
4. **Prove it works.** Tests for the behaviour and its edge cases (a test for every row of the
   agreed behaviour tables), and seeing it work for real: drive the UI in a browser (desktop and
   mobile widths, light and dark), call the API or run the CLI, and check the desktop app when it
   is affected. Report what was verified, how, and anything that couldn't be.
5. **Review** as in the Definition of done: the `code-reviewer` agent, fixes, re-verification.

A **small fix** (a bug with an obvious fix, a typo, a one-line change) can go straight to step 3,
but it is still proven to work (step 4): a test for the case it fixes, no diagram needed.

## Specifying behaviour

We agree what should happen before building it, so "done" can be checked rather than felt. Use
the form that fits the change; don't invent states where there are none.

- **State machines where there is one:** a flow, a request, a lifecycle, anything that can be
  rejected. Draw the states and the events that move between them, as Mermaid. Keep one small
  machine per concern (a request, a session's notes, a recording), never one diagram of
  everything.
- **A table of every state and event.** Each cell is a transition, a rejection (with what the user
  or caller is told), or "can't happen" with the reason. Rejections are rows too: missing,
  damaged, unreadable, busy, not allowed.
- **One test per row,** named after the row, transitions and rejections alike, so a missing test
  shows. Keep the decision in a small pure function where it fits, and test it directly.
- **Inputs, not states,** for parsers, search, text handling and other input-to-output code: a
  table of the inputs that matter (empty, huge, odd line breaks, Unicode, malformed) with the
  expected output, plus property-based tests (e.g. `hypothesis`, added when first needed) where
  random input finds what examples miss.
- **The diagram lives with the code:** in the feature's `docs/` page, updated in the same change.

## Engineering practices

- **Keep it simple.** Build the smallest thing that does the job. No options, layers or
  abstractions "for later": add them when a second real use appears. Between two designs, pick the
  one with fewer moving parts.
- **Best practice over quick fixes.** Fix a problem the way the platform or library documents it,
  even when a workaround is quicker: simple means fewer moving parts, not least effort. If the
  right fix is bigger than the task, say so and let the user choose; never ship the shortcut quietly.
- **Reuse, don't rebuild.** Before writing something new, look for what Iris already has
  (components, CSS classes and tokens in `index.css`, helpers like `act()`, `write_model`,
  `ConfirmDialog`) and then for a well-kept library. Extend what exists instead of making a
  near-copy.
- **Change only what the task needs.** No drive-by refactors, renames or reformatting; note them
  and do them separately, unless the user asks. Remove code a change leaves unused. The one
  exception to this rule: removing, from a file you work on, the comments and docstrings that
  *No comments* doesn't keep.
- **One source of truth.** Settings, file names, wording and constants live in one place
  (`Settings`, `store/layout.py`, `session/messages.ts`) and are imported, never copied.
- **Pure logic, thin edges.** Keep decisions in small pure functions (no I/O) that plain
  `pytest` / `node --test` can cover; keep routes, components and the Electron main process thin.
- **Check at the boundary, trust inside.** Validate what comes in (API requests, files on disk,
  model output) once, where it enters; code behind that works with typed, valid data.
- **Fail clearly.** Developers get the full error in the log; the user gets plain words and a way
  to recover. Never swallow an error silently.
- **Shared code has a home.** A building block used by more than one stage, lane or entry point
  lives in a shared module (`llm/` for model calls, `store/` for file names and reading and
  writing files, `speech.py`, `svg.py`), never inside a stage, lane or route that the others then
  import from or copy. Stages and lanes hold only their own decisions. When two callers differ in
  one step (where the input comes from, how an error is handled), share the rest and pass that
  step in, as a small `Protocol` or a function argument, the way `Source` does for uploads and
  live recordings. An interface earns its place with two real implementations, not one.
- **No comments** in hand-written code and tests (`backend/`, `frontend/`, `desktop/`): no comments
  in any form (`#`, `//`, `/* */`, JSDoc, HTML and CSS comments) and no docstrings, except the ones
  something reads (below), which new code writes too. Name things for what they mean to the user or
  the domain. A reason is kept only by renaming something that already has a name (a variable,
  parameter, attribute or existing function), by a test, or, if it is a design decision, in the
  feature's `docs/` page in plain words; anything else is dropped. Never add a function, variable,
  alias or constant just to hold a reason. Tool directives stay, with nothing after them (shebangs,
  `# noqa`, `# type: ignore`, `// eslint-disable`). Generated files (`schema.d.ts`) and config and
  example files of any format (`.env.example`, `pyproject.toml`, `vite.config.ts`, `tsconfig*.json`,
  `electron-builder.yml`, `Makefile`, CI workflows) keep their comments. When you work on a file,
  remove its other comments and docstrings. The docstrings that stay:
  - MCP tool docstrings: the assistant reads them as the tools' descriptions;
  - docstrings of FastAPI routes and of the models the API sends or accepts (the ones that reach
    `schema.d.ts`): they become the OpenAPI schema;
  - docstrings of models sent to a model as its output schema (structured output): they become
    the schema's descriptions, which the model reads and which are part of cache keys;
  - the module or class docstring that states an interface's contract (today `sources/base.py`
    and `live/lane.py`), not its methods.

## Conventions

- **Stage contract.** A pipeline stage is `async def run(ctx: StageContext) -> None`. It reads files from
  the session folder and writes only its own outputs and caches (named in `store/layout.py`, typed in
  `models.py`), writing JSON atomically with `write_model`. No stage reaches into another stage's
  internals.
- **Accurate visuals** (`docs/overview.md`, principle 1). What was shown is rebuilt as a
  professional visual. A redraw reaches the notes only after a vision check against the source crop
  and brief passes. If none passes, the source crop is shown instead and recorded as unverified in
  `renders.json`. Source crops stay on disk so any visual can be checked or regenerated.
- **Model output is untrusted.** Validate it with Pydantic, drop unknown ids, sanitize SVG with
  `iris.svg.sanitize_svg`. Prompts treat session content as data, never instructions.
- **No system dependencies.** Media goes through PyAV (it bundles FFmpeg). No `ffmpeg` CLI, no
  `subprocess` for media, nothing a user must `brew install`.
- **CPU-bound work** (decoding, OpenCV, SVG rasterizing) runs in `asyncio.to_thread`.
- **Model calls** go through `iris.llm.client.get_openai`. Model IDs come from `Settings`, never
  hard-coded in stages.
- **Tests never call real paid APIs.** Use fakes or monkeypatching. Generate media fixtures in
  `tmp_path` (synthetic video/audio/frames); don't commit large binaries.
- **Docs are overviews.** `docs/` explains the shape, the flow and the decisions with their why;
  the detail stays in the code. A change that alters a flow or a decision updates its page. Links
  run one way: a page's "Where to look" points at the code; code never points at a docs page.
- **Commits:** one short single-line message, `<type>(<domain>): <comment>`, e.g.
  `fix(render): retry transient API errors per figure`. No body, no `Co-Authored-By`, no AI
  attribution.
  - `type`: `feat`, `fix`, `docs`, `chore`, `ci`, `refactor`, `test`, `perf`, `build`, `style` or
    `revert`.
  - `domain`: lowercase area changed: a stage (`ingest`, `transcribe`, `detect`, `align`, `select`,
    `write`, `render`, `assemble`, `synthesize`), or `api`, `live`, `store`, `frontend`, `backend`,
    `docs`, `ci`, `repo` for changes across areas.
  - `comment`: imperative, starting with a lowercase letter or digit, no trailing full stop.
- **Branches:** one branch per task (`feat/…`, `fix/…`, `ci/…`, `chore/…`). Never commit to `main`
  directly. Merged PR branches are deleted from GitHub automatically.

## Checks (must pass before review)

CI (`.github/workflows/ci.yml`) runs the same checks and must pass before merging.

```sh
cd backend && uv run ruff format . && uv run ruff check . && uv run pytest -q
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
cd desktop && npm test
```

If you change the API, run `make gen-api` with the backend running and commit `schema.d.ts`.

## Definition of done

A branch is ready to merge only when all of these hold:

1. **Checks pass** locally and in CI.
2. **Tests cover every row** of the agreed state machines and input tables, transitions and
   rejections alike, and the edge cases in `docs/review-checklist.md` that apply.
3. **Shown working:** the change was exercised for real (browser, API, CLI or desktop app), and the
   PR says what was verified and how.
4. **Independent review:** the `code-reviewer` agent (`.claude/agents/code-reviewer.md`) reviewed
   the branch diff against `main` with fresh context, checking the code against the agreed tables
   (a missing row, or behaviour that differs from its cell, is a should-fix), and every blocker
   and should-fix finding is fixed or explicitly waived by the user.
5. **Re-verified:** after fixes, the reviewer confirmed each finding is resolved.
6. **Human merge:** a PR is opened with a summary, test results, what was verified and the review
   outcome. The user merges.
