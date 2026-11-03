"""Command line entry point.

iris serve                          run the API (http://localhost:8000)
iris process <file>                 create a session from a recording and run the pipeline
iris run <session_id> [--from S]    re-run a session's pipeline, optionally from stage S
iris stage <session_id> <stage>     run one stage on its own (reads/writes session files)
iris cost <session_id>              model usage and cost per stage, model and purpose
"""

import argparse
import asyncio
import sys
from pathlib import Path

from iris.config import get_settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.llm.usage import read_usage
from iris.models import SessionUsage, SourceKind, Stage, UsageTotals
from iris.sources.upload import save_upload
from iris.store.db import SessionStore
from iris.store.layout import session_paths


def _runner() -> JobRunner:
    settings = get_settings()
    settings.sessions_dir.mkdir(parents=True, exist_ok=True)
    return JobRunner(SessionStore(settings.db_path), EventBus(), settings)


async def _process(file: Path) -> None:
    runner = _runner()
    session = runner.store.create(file.stem, SourceKind.UPLOAD)
    paths = session_paths(runner.settings.sessions_dir, session.id).ensure()
    with file.open("rb") as f:
        save_upload(paths, file.name, f)
    print(f"session {session.id}")
    await runner.run(session.id)
    print(runner.store.get(session.id))


async def _run(session_id: str, from_stage: Stage | None) -> None:
    runner = _runner()
    await runner.run(session_id, from_stage)
    print(runner.store.get(session_id))


async def _stage(session_id: str, stage: Stage) -> None:
    runner = _runner()
    await runner.run_stage(runner.context(session_id), stage)
    print(f"{stage} done")


# --- iris cost --------------------------------------------------------------------------------


def _usd(totals: UsageTotals) -> str:
    cost = f"${totals.cost_usd:.4f}"
    return f"{cost} + {totals.unpriced_calls} unpriced" if totals.unpriced_calls else cost


def _table(title: str, rows: dict[str, UsageTotals], total: UsageTotals) -> list[str]:
    header = ("calls", "input", "cached", "output", "reasoning", "images", "audio s", "time s")
    width = max([len(title), len("total"), *map(len, rows)])
    lines = [f"{title:<{width}}  " + "  ".join(f"{h:>9}" for h in header) + "  cost"]
    for name, t in [*rows.items(), ("total", total)]:
        values = (
            t.calls,
            t.input_tokens,
            t.cached_input_tokens,
            t.output_tokens,
            t.reasoning_tokens,
            t.images,
            f"{t.audio_seconds:.0f}",
            f"{t.latency_s:.1f}",
        )
        lines.append(f"{name:<{width}}  " + "  ".join(f"{v:>9}" for v in values) + f"  {_usd(t)}")
    return lines


def format_usage(session_id: str, usage: SessionUsage) -> str:
    lines = [f"session {session_id}: {_usd(usage.total)} over {usage.total.calls} model calls"]
    if not usage.records:
        return lines[0] + " (none recorded)"
    for title, rows in (
        ("stage", usage.by_stage),
        ("model", usage.by_model),
        ("purpose", usage.by_purpose),
    ):
        lines += ["", *_table(title, rows, usage.total)]
    unpriced = sorted({r.unpriced for r in usage.records if r.unpriced})
    if unpriced:
        lines += ["", "Unpriced calls (not included in cost):", *(f"  {u}" for u in unpriced)]
    if usage.total.failed_calls:
        lines += ["", f"{usage.total.failed_calls} calls failed."]
    lines += ["", f"Prices checked against OpenAI's pricing page on {usage.prices_checked}."]
    return "\n".join(lines)


def _cost(session_id: str) -> int:
    paths = session_paths(get_settings().sessions_dir, session_id)
    if not paths.root.is_dir():
        print(f"no session {session_id}", file=sys.stderr)
        return 1
    try:
        usage = read_usage(paths.usage)
    except ValueError as exc:
        print(f"{paths.usage} is unreadable: {exc}", file=sys.stderr)
        return 1
    print(format_usage(session_id, usage))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="iris")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")

    process = sub.add_parser("process")
    process.add_argument("file", type=Path)

    run = sub.add_parser("run")
    run.add_argument("session_id")
    run.add_argument("--from", dest="from_stage", type=Stage, choices=list(Stage))

    stage = sub.add_parser("stage")
    stage.add_argument("session_id")
    stage.add_argument("stage", type=Stage, choices=list(Stage))

    cost = sub.add_parser("cost")
    cost.add_argument("session_id")

    args = parser.parse_args()
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("iris.main:app", host=args.host, port=args.port, reload=args.reload)
    elif args.cmd == "process":
        asyncio.run(_process(args.file))
    elif args.cmd == "run":
        asyncio.run(_run(args.session_id, args.from_stage))
    elif args.cmd == "stage":
        asyncio.run(_stage(args.session_id, args.stage))
    elif args.cmd == "cost":
        sys.exit(_cost(args.session_id))


if __name__ == "__main__":
    main()
