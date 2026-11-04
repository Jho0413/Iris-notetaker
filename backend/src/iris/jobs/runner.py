"""Runs a session's pipeline as an in-process asyncio task. Single user, so no queue server."""

import asyncio
import logging

from iris.config import Settings
from iris.events import EventBus
from iris.live import LiveRegistry
from iris.llm import usage
from iris.models import EventType, SessionStatus, SourceKind, Stage
from iris.pipeline import STAGES, plan_from
from iris.pipeline.context import StageContext
from iris.store.db import SessionStore
from iris.store.layout import session_paths

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, store: SessionStore, bus: EventBus, settings: Settings):
        self.store = store
        self.bus = bus
        self.settings = settings
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Processors of live sessions being recorded (iris/live/); finished when the run starts.
        self.live = LiveRegistry(self.context, enabled=settings.live_processing)

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def start(self, session_id: str, from_stage: Stage | None = None) -> None:
        if self.is_running(session_id):
            raise RuntimeError(f"session {session_id} is already processing")
        task = asyncio.create_task(self.run(session_id, from_stage), name=f"session-{session_id}")
        self._tasks[session_id] = task

    def context(self, session_id: str) -> StageContext:
        session = self.store.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return StageContext(
            session_id=session_id,
            source=session.source,
            paths=session_paths(self.settings.sessions_dir, session_id).ensure(),
            settings=self.settings,
            emit=lambda type, data: self.bus.publish(session_id, type, data),
        )

    async def run(self, session_id: str, from_stage: Stage | None = None) -> None:
        ctx = self.context(session_id)
        self.store.clear_error(session_id)
        self._set_status(session_id, SessionStatus.PROCESSING)
        try:
            if ctx.source == SourceKind.BROWSER_LIVE:
                # Recording has stopped: let the live lanes flush before ingest reads live/.
                # Promoting their outputs (so only the tail is new work) is not built yet, so the
                # full plan runs and reuses model caches where inputs match.
                await self.live.finish(session_id)
            plan = plan_from(from_stage)
            if any(Stage.SELECT in group for group in plan):
                # The selection is made again, so visual ids may change: a draft written against
                # the old one (live, or by an earlier run) must not be served meanwhile.
                ctx.paths.notes_draft.unlink(missing_ok=True)
            for group in plan:
                await asyncio.gather(*(self.run_stage(ctx, s) for s in group))
        except Exception as exc:
            log.exception("session %s failed", session_id)
            message = str(exc) if isinstance(exc, StageError) else f"{type(exc).__name__}: {exc}"
            self.store.update(session_id, status=SessionStatus.FAILED, error=message)
            self.bus.publish(session_id, EventType.ERROR, {"message": message})
            self.bus.publish(session_id, EventType.STATUS, {"status": SessionStatus.FAILED})
            return
        self._set_status(session_id, SessionStatus.DONE)

    async def run_stage(self, ctx: StageContext, stage: Stage) -> None:
        self.store.update(ctx.session_id, current_stage=stage)
        self.bus.publish(ctx.session_id, EventType.STAGE_STARTED, {"stage": stage})
        try:
            with usage.recording(ctx, stage):  # model calls in the stage go to usage.json
                await STAGES[stage](ctx)
        except Exception as exc:
            raise StageError(stage, exc) from exc
        self.bus.publish(ctx.session_id, EventType.STAGE_FINISHED, {"stage": stage})

    def _set_status(self, session_id: str, status: SessionStatus) -> None:
        self.store.update(session_id, status=status)
        self.bus.publish(session_id, EventType.STATUS, {"status": status})

    async def shutdown(self) -> None:
        await self.live.shutdown()
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


class StageError(Exception):
    def __init__(self, stage: Stage, cause: Exception):
        super().__init__(f"{stage} failed: {type(cause).__name__}: {cause}")
        self.stage = stage
