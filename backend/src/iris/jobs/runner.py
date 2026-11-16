import asyncio
import logging

from iris.aio import cancel_and_wait
from iris.config import Settings
from iris.events import EventBus
from iris.live import LiveRegistry, LiveTimeline, promote
from iris.llm import usage
from iris.llm.errors import StageError, UserFacingError, first_error
from iris.models import (
    GENERIC_FAILURE,
    INTERRUPTED_RUN,
    EventType,
    FailureKind,
    SessionStatus,
    SourceKind,
    Stage,
)
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
        self.live = LiveRegistry(self.context, enabled=settings.live_processing)

    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def start(self, session_id: str, from_stage: Stage | None = None) -> None:
        if self.is_running(session_id):
            raise RuntimeError(f"session {session_id} is already processing")
        task = asyncio.create_task(self.run(session_id, from_stage), name=f"session-{session_id}")
        self._tasks[session_id] = task

    def fail_interrupted(self) -> None:
        for session in self.store.list():
            if session.status != SessionStatus.PROCESSING:
                continue
            stage = session.current_stage or "the start"
            log.warning("session %s was interrupted at %s: marked failed", session.id, stage)
            self.store.fail(
                session.id,
                message=INTERRUPTED_RUN,
                kind=FailureKind.GENERAL,
                detail=f"interrupted: Iris stopped during the run (at {stage})",
            )

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
            timeline = None
            if ctx.source == SourceKind.BROWSER_LIVE:
                timeline = await self.live.finish(session_id)
            if timeline is not None and from_stage is None:
                await self._run_live(ctx, timeline)
            else:
                await self._run_plan(ctx, plan_from(from_stage))
        except Exception as exc:
            log.exception("session %s failed", session_id)
            self._fail(session_id, exc)
            return
        self.store.clear_error(session_id)
        self._set_status(session_id, SessionStatus.DONE)

    def _fail(self, session_id: str, exc: BaseException) -> None:
        exc = first_error(exc)
        stage = exc.stage if isinstance(exc, StageError) else None
        cause = exc.cause if isinstance(exc, StageError) else exc
        if isinstance(cause, UserFacingError):
            message, kind = cause.message, FailureKind.INPUT
        else:
            message, kind = GENERIC_FAILURE, FailureKind.GENERAL
        detail = str(exc) if isinstance(exc, StageError) else f"{type(exc).__name__}: {exc}"
        self.store.fail(session_id, message=message, kind=kind, detail=detail, stage=stage)
        data: dict[str, object] = {"message": message, "kind": kind}
        if stage is not None:
            data["stage"] = stage
        self.bus.publish(session_id, EventType.ERROR, data)
        self.bus.publish(session_id, EventType.STATUS, {"status": SessionStatus.FAILED})

    async def _run_plan(self, ctx: StageContext, plan: list[list[Stage]]) -> None:
        if any(Stage.SELECT in group for group in plan):
            ctx.paths.notes_draft.unlink(missing_ok=True)
        for group in plan:
            try:
                async with asyncio.TaskGroup() as tasks:
                    for stage in group:
                        tasks.create_task(self.run_stage(ctx, stage))
            except BaseExceptionGroup as error:
                raise first_error(error) from None

    async def _run_live(self, ctx: StageContext, timeline: LiveTimeline) -> None:
        await self.run_stage(ctx, Stage.INGEST)
        promoted = promote.promotable(ctx, timeline)
        log.info(
            "session %s: promoting %s (lanes finished %s, failed %s, skipped input %s)",
            ctx.session_id,
            [str(s) for s in promoted] or "nothing",
            sorted(map(str, timeline.finished)),
            sorted(map(str, timeline.failed)),
            sorted(map(str, timeline.skipped_input)),
        )
        for promoting, running in promote.live_steps(promoted):
            for stage in promoting:
                await self.promote_stage(ctx, timeline, stage)
            await self._run_plan(ctx, [running])

    async def promote_stage(self, ctx: StageContext, timeline: LiveTimeline, stage: Stage) -> None:
        self.store.update(ctx.session_id, current_stage=stage)
        data = {"stage": stage, "promoted": True}
        self.bus.publish(ctx.session_id, EventType.STAGE_STARTED, data)
        try:
            extra = await promote.promote(ctx, timeline, stage)
        except Exception as exc:
            raise StageError(stage, exc) from exc
        self.bus.publish(ctx.session_id, EventType.STAGE_FINISHED, {**extra, **data})

    async def run_stage(self, ctx: StageContext, stage: Stage) -> None:
        self.store.update(ctx.session_id, current_stage=stage)
        self.bus.publish(ctx.session_id, EventType.STAGE_STARTED, {"stage": stage})
        try:
            with usage.recording(ctx, stage):
                await STAGES[stage](ctx)
        except Exception as exc:
            raise StageError(stage, exc) from exc
        self.bus.publish(ctx.session_id, EventType.STAGE_FINISHED, {"stage": stage})

    def _set_status(self, session_id: str, status: SessionStatus) -> None:
        self.store.update(session_id, status=status)
        self.bus.publish(session_id, EventType.STATUS, {"status": status})

    async def shutdown(self) -> None:
        await self.live.shutdown()
        await cancel_and_wait(self._tasks.values())
