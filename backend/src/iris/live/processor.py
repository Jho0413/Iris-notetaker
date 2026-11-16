import asyncio
import logging
from collections.abc import Callable, Sequence

from iris.aio import cancel_and_wait
from iris.live.audio import AudioLane
from iris.live.draft import DraftLane
from iris.live.lane import LiveLane
from iris.live.screen import ScreenLane
from iris.live.select import SelectLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm.errors import retries_until
from iris.pipeline.context import StageContext

log = logging.getLogger(__name__)

LaneFactory = Callable[[StageContext, LiveTimeline], Sequence[LiveLane]]


def default_lanes(ctx: StageContext, timeline: LiveTimeline) -> list[LiveLane]:
    return [cls(ctx, timeline) for cls in (AudioLane, ScreenLane, SelectLane, DraftLane)]


class LiveProcessor:
    def __init__(
        self,
        ctx: StageContext,
        lanes: LaneFactory = default_lanes,
        finish_timeout_s: float | None = None,
    ):
        self.ctx = ctx
        self.timeline = LiveTimeline()
        self.finish_timeout_s = (
            ctx.settings.live_finish_timeout_s if finish_timeout_s is None else finish_timeout_s
        )
        self.lanes: dict[Lane, LiveLane] = {}
        for lane in lanes(ctx, self.timeline):
            if lane.lane in self.lanes:
                raise ValueError(f"two {lane.lane} lanes")
            self.lanes[lane.lane] = lane
        self.timeline.absent.update(set(Lane) - self.lanes.keys())
        self._tasks: dict[Lane, asyncio.Task[None]] = {}

    def start(self) -> None:
        if self._tasks:
            return
        self._tasks = {
            name: asyncio.create_task(
                self._run_lane(lane), name=f"live-{self.ctx.session_id}-{name}"
            )
            for name, lane in self.lanes.items()
        }

    async def _run_lane(self, lane: LiveLane) -> None:
        tl = self.timeline

        def retries_over() -> bool:
            return lane.halted or (lane.retries_end_at_notes and tl.ending)

        async def pause(delay: float) -> bool:
            return not await tl.wait_until(retries_over, delay)

        try:
            with retries_until(retries_over, pause):
                await lane.run()
            if not self.timeline.ending:
                raise RuntimeError("the lane returned before notes were asked for")
        except asyncio.CancelledError:
            await self.timeline.fail_lane(lane.lane, "cancelled")
            raise
        except Exception as exc:
            log.exception("session %s: live %s lane failed", self.ctx.session_id, lane.lane)
            await self.timeline.fail_lane(lane.lane, f"{type(exc).__name__}: {exc}")
        else:
            await self.timeline.finish_lane(lane.lane)
        finally:
            if not self.timeline.done(lane.lane):
                self.timeline.failed[lane.lane] = "stopped"

    def notify(self, lane: Lane) -> None:
        if live_lane := self.lanes.get(lane):
            live_lane.wake()

    async def finish(self) -> LiveTimeline:
        await self.timeline.request_end()
        for lane in self.lanes.values():
            lane.wake()
        tasks = list(self._tasks.values())
        if tasks:
            try:
                _, pending = await asyncio.wait(tasks, timeout=self.finish_timeout_s)
            except asyncio.CancelledError:
                await self.cancel()
                raise
            if pending:
                log.warning(
                    "session %s: live lanes %s did not finish in %ss; cancelled",
                    self.ctx.session_id,
                    sorted(name for name, t in self._tasks.items() if t in pending),
                    self.finish_timeout_s,
                )
                await cancel_and_wait(pending)
        return self.timeline

    async def cancel(self) -> None:
        await cancel_and_wait(self._tasks.values())


class LiveRegistry:
    def __init__(
        self,
        context: Callable[[str], StageContext],
        enabled: bool = True,
        lanes: LaneFactory = default_lanes,
    ):
        self._context = context
        self.enabled = enabled
        self._lanes = lanes
        self._processors: dict[str, LiveProcessor] = {}

    def get(self, session_id: str) -> LiveProcessor | None:
        return self._processors.get(session_id)

    def ensure(self, session_id: str) -> LiveProcessor | None:
        if not self.enabled:
            return None
        processor = self._processors.get(session_id)
        if processor is None:
            processor = LiveProcessor(self._context(session_id), self._lanes)
            self._processors[session_id] = processor
            processor.start()
        return processor

    async def finish(self, session_id: str) -> LiveTimeline | None:
        processor = self._processors.pop(session_id, None)
        if processor is None:
            return None
        return await processor.finish()

    async def cancel(self, session_id: str) -> None:
        processor = self._processors.pop(session_id, None)
        if processor is not None:
            await processor.cancel()

    async def shutdown(self) -> None:
        processors, self._processors = list(self._processors.values()), {}
        await asyncio.gather(*(p.cancel() for p in processors), return_exceptions=True)
