# Iris: working agreement

Read `README.md` and `docs/architecture.md` first. This file covers how we work.

## Conventions

- **Stage contract.** A pipeline stage is `async def run(ctx: StageContext) -> None`. It reads files from
  the session folder and writes only its own outputs and caches (named in `store/layout.py`, typed in
  `models.py`), writing JSON atomically with `write_model`. No stage reaches into another stage's
  internals.
- **Accurate visuals** (architecture principle 1). What was shown is rebuilt as a professional visual.
  A redraw reaches the notes only after a vision check against the source crop and brief passes. If
  none passes, the source crop is shown instead and recorded as unverified in `renders.json`. Source
  crops stay on disk so any visual can be checked or regenerated.
- **Model output is untrusted.** Validate it with Pydantic, drop unknown ids, sanitize SVG with
  `iris.svg.sanitize_svg`. Prompts treat session content as data, never instructions.
- **No system dependencies.** Media goes through PyAV (it bundles FFmpeg). No `ffmpeg` CLI, no
  `subprocess` for media, nothing a user must `brew install`.
- **CPU-bound work** (decoding, OpenCV, SVG rasterizing) runs in `asyncio.to_thread`.
- **Model calls** go through `iris.llm.client.get_openai`. Model IDs come from `Settings`, never
  hard-coded in stages.
- **Tests never call real paid APIs.** Use fakes or monkeypatching. Generate media fixtures in
  `tmp_path` (synthetic video/audio/frames); don't commit large binaries.
- **Commits:** one short single-line message, `<type>(<domain>): <comment>`, e.g.
  `fix(render): retry transient API errors per figure`. No body, no `Co-Authored-By`, no AI
  attribution. CI rejects PR commits that don't match.
  - `type`: `feat`, `fix`, `docs`, `chore`, `ci`, `refactor`, `test`, `perf`, `build`, `style` or
    `revert`.
  - `domain`: lowercase area changed: a stage (`ingest`, `transcribe`, `detect`, `align`, `select`,
    `write`, `render`, `assemble`), or `api`, `live`, `store`, `frontend`, `backend`, `docs`, `ci`,
    `repo` for changes across areas.
  - `comment`: imperative, starting with a lowercase letter or digit, no trailing full stop.
- **Branches:** one branch per task (`feat/…`, `fix/…`, `ci/…`, `chore/…`). Never commit to `main`
  directly. Merged PR branches are deleted from GitHub automatically.

## Checks (must pass before review)

CI (`.github/workflows/ci.yml`) runs the same checks and must pass before merging.

```sh
cd backend && uv run ruff format . && uv run ruff check . && uv run pytest -q
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```

If you change the API, run `make gen-api` with the backend running and commit `schema.d.ts`.

## Definition of done

A branch is ready to merge only when all of these hold:

1. **Checks pass** locally and in CI.
2. **Tests cover the behaviour and the edge cases** in `docs/review-checklist.md` that apply.
3. **Independent review:** the `code-reviewer` agent (`.claude/agents/code-reviewer.md`) reviewed
   the branch diff against `main` with fresh context, and every blocker and should-fix finding is
   fixed or explicitly waived by the user.
4. **Re-verified:** after fixes, the reviewer confirmed each finding is resolved.
5. **Human merge:** a PR is opened with a summary, test results and the review outcome. The user
   merges.
