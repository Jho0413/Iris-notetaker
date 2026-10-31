"""Command line entry point.

iris serve                          run the API (http://localhost:8000)
iris process <file>                 create a session from a recording and run the pipeline
iris run <session_id> [--from S]    re-run a session's pipeline, optionally from stage S
iris stage <session_id> <stage>     run one stage on its own (reads/writes session files)
"""

import argparse
import asyncio
from pathlib import Path

from iris.config import get_settings
from iris.events import EventBus
from iris.jobs.runner import JobRunner
from iris.models import SourceKind, Stage
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


if __name__ == "__main__":
    main()
