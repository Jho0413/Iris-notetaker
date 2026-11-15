import argparse
import asyncio
import sys
from pathlib import Path

from iris.config import Settings, get_settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.launch_secrets import SecretsError, read_secrets, settings_with_secrets
from iris.llm.usage import read_usage
from iris.models import Session, SessionStatus, SessionUsage, SourceKind, Stage, UsageTotals
from iris.sources.upload import save_upload
from iris.store.db import FailureDetail, SessionStore
from iris.store.layout import session_paths
from iris.user_settings import load_settings


def _runner() -> JobRunner:
    settings = load_settings()
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


def _no_session(session_id: str) -> int:
    print(f"no session {session_id}", file=sys.stderr)
    return 1


async def _run(session_id: str, from_stage: Stage | None) -> int:
    runner = _runner()
    if runner.store.get(session_id) is None:
        return _no_session(session_id)
    await runner.run(session_id, from_stage)
    print(runner.store.get(session_id))
    return 0


async def _stage(session_id: str, stage: Stage) -> int:
    runner = _runner()
    if runner.store.get(session_id) is None:
        return _no_session(session_id)
    await runner.run_stage(runner.context(session_id), stage)
    print(f"{stage} done")
    return 0


def _serve(host: str, port: int, reload: bool, secrets_from_stdin: bool) -> int:
    import uvicorn

    if not secrets_from_stdin:
        uvicorn.run("iris.main:app", host=host, port=port, reload=reload)
        return 0
    if reload:
        print("iris serve: --reload can't be used with --secrets-from-stdin", file=sys.stderr)
        return 2
    try:
        settings = settings_with_secrets(read_secrets(sys.stdin.buffer))
    except SecretsError as exc:
        print(f"iris serve: {exc}", file=sys.stderr)
        return 2
    from iris.main import create_app

    uvicorn.run(create_app(settings), host=host, port=port)
    return 0


def format_show(session: Session, detail: FailureDetail | None) -> str:
    rows = [
        ("session", session.id),
        ("title", session.title),
        ("source", session.source),
        ("status", session.status),
        ("stage", session.current_stage or "-"),
    ]
    if session.status == SessionStatus.FAILED or detail is not None:
        rows += [
            ("failure kind", session.failure_kind or "-"),
            ("user message", session.failure_message or "-"),
            ("raw error", detail.error if detail else "-"),
            ("failed at", (detail.at if detail else None) or "unknown"),
        ]
    width = max(len(name) for name, _ in rows)
    return "\n".join(f"{name:<{width}}  {value}" for name, value in rows)


def _show(session_id: str) -> int:
    store = SessionStore(get_settings().db_path)
    try:
        session = store.get(session_id)
        if session is None:
            return _no_session(session_id)
        print(format_show(session, store.failure_detail(session_id)))
    finally:
        store.close()
    return 0


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


def _cost(session_id: str | None) -> int:
    settings = get_settings()
    if session_id is None:
        store = SessionStore(settings.db_path)
        try:
            sessions = store.list()
        finally:
            store.close()
        if not sessions:
            print("no sessions yet", file=sys.stderr)
            return 1
        session_id = sessions[0].id
    paths = session_paths(settings.sessions_dir, session_id)
    if not paths.root.is_dir():
        return _no_session(session_id)
    print(format_usage(session_id, read_usage(paths.usage_log)))
    return 0


def _mcp() -> None:
    from iris.mcp_server import create_server

    create_server(Settings(_env_file=None)).run()


def main() -> None:
    parser = argparse.ArgumentParser(prog="iris")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument(
        "--secrets-from-stdin",
        action="store_true",
        help="read the OpenAI key and API token from one JSON line on stdin (the desktop app)",
    )

    process = sub.add_parser("process")
    process.add_argument("file", type=Path)

    run = sub.add_parser("run")
    run.add_argument("session_id")
    run.add_argument("--from", dest="from_stage", type=Stage, choices=list(Stage))

    stage = sub.add_parser("stage")
    stage.add_argument("session_id")
    stage.add_argument("stage", type=Stage, choices=list(Stage))

    cost = sub.add_parser("cost")
    cost.add_argument("session_id", nargs="?", help="defaults to the most recent session")

    show = sub.add_parser("show", help="a session's status and raw error (for developers)")
    show.add_argument("session_id")

    sub.add_parser("mcp", help="a read-only MCP server on stdin/stdout, for AI assistants")

    replay = sub.add_parser("replay-live", help="play recordings into a running backend's live API")
    replay.add_argument("files", type=Path, nargs="+")
    replay.add_argument("--url", default="http://127.0.0.1:8000", help="the backend to replay into")
    replay.add_argument("--speed", type=float, default=1.0, help="playback speed (1 = real time)")

    args = parser.parse_args()
    if args.cmd == "serve":
        sys.exit(_serve(args.host, args.port, args.reload, args.secrets_from_stdin))
    elif args.cmd == "process":
        asyncio.run(_process(args.file))
    elif args.cmd == "run":
        sys.exit(asyncio.run(_run(args.session_id, args.from_stage)))
    elif args.cmd == "stage":
        sys.exit(asyncio.run(_stage(args.session_id, args.stage)))
    elif args.cmd == "cost":
        sys.exit(_cost(args.session_id))
    elif args.cmd == "show":
        sys.exit(_show(args.session_id))
    elif args.cmd == "mcp":
        _mcp()
    elif args.cmd == "replay-live":
        from iris.live import replay as live_replay

        sys.exit(live_replay.main(args.url, args.files, args.speed))


if __name__ == "__main__":
    main()
