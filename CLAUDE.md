# Iris: working agreement

Read `README.md` and `docs/architecture.md` first. This file covers how we work.

## Conventions

- **Stage contract.** A pipeline stage is `async def run(ctx: StageContext) -> None`. It reads files from
  the session folder and writes exactly its own output (named in `store/layout.py`, typed in
  `models.py`) with `write_model`. No stage reaches into another stage's internals.
- **Faithful visuals.** Every image in the notes is a real frame or a crop of one. Never generate,
  redraw or annotate images.
- **No system dependencies.** Media goes through PyAV (it bundles FFmpeg). No `ffmpeg` CLI, no
  `subprocess` for media, nothing a user must `brew install`.
- **CPU-bound work** (decoding, OpenCV) runs in `asyncio.to_thread`.
- **Model calls** go through `iris.llm.client.get_openai`. Model IDs come from `Settings`, never
  hard-coded in stages.
- **Tests never call real paid APIs.** Use fakes or monkeypatching. Generate media fixtures in
  `tmp_path` (synthetic video/audio/frames); don't commit large binaries.
- **Commits:** one short single-line message. No body, no `Co-Authored-By`, no AI attribution.
- **Branches:** one branch per task (`feat/…`, `fix/…`, `ci/…`, `chore/…`). Never commit to `main`
  directly.

## Checks (must pass before review)

```sh
cd backend && uv run ruff format . && uv run ruff check . && uv run pytest -q
cd frontend && npm run lint && npm run typecheck && npm run build
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
