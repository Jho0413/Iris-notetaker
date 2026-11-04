"""One live session's processor, and the registry of processors for sessions being recorded.

``LiveProcessor`` runs the lanes (``iris/live/``) as asyncio tasks that share one
``LiveTimeline``. The API wakes a lane when its input arrives; the job runner calls ``finish``
when recording stops, before processing the session. A failing lane is logged and marked failed
on the timeline: the recording carries on, and stop promotes less of the live work (only the
transcript, or nothing) and runs more of the pipeline (``live/promote.py``; architecture section
1, "Life of a session: live").
"""

import asyncio
import logging
from collections.abc import Callable, Sequence

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
        # A lane this processor doesn't run publishes nothing: it is done from the start, so a
        # lane waiting on it is never stuck (no one is waiting yet, so no notify is needed). It
        # is not finished, so the timeline is never complete without it.
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
        """Run one lane; however it ends, it ends finished or failed, never neither."""
        tl = self.timeline

        def stopped() -> bool:  # no more retries: nothing can be promoted, or (draft) stop
            return tl.upstream_failed(lane.lane) or (lane.retries_end_at_stop and tl.ending)

        async def pause(delay: float) -> bool:
            try:
                await asyncio.wait_for(tl.wait_until(stopped), delay)
            except TimeoutError:
                return True
            return False

        try:
            with retries_until(stopped, pause):
                await lane.run()
            if not self.timeline.ending:
                raise RuntimeError("the lane returned before stop was requested")
        except asyncio.CancelledError:
            await self.timeline.fail_lane(lane.lane, "cancelled")
            raise
        except Exception as exc:
            log.exception("session %s: live %s lane failed", self.ctx.session_id, lane.lane)
            await self.timeline.fail_lane(lane.lane, f"{type(exc).__name__}: {exc}")
        else:
            await self.timeline.finish_lane(lane.lane)
        finally:
            if not self.timeline.done(lane.lane):  # e.g. cancelled while failing
                self.timeline.failed[lane.lane] = "stopped"

    def notify_audio(self) -> None:
        if lane := self.lanes.get(Lane.AUDIO):
            lane.wake()

    def notify_frame(self) -> None:
        if lane := self.lanes.get(Lane.SCREEN):
            lane.wake()

    async def finish(self) -> LiveTimeline:
        """Ask every lane to flush, wait for all of them, and return the final timeline.

        Lanes still running after ``finish_timeout_s`` are cancelled and marked failed, so a stuck
        lane delays stop by at most that long; stop then redoes that lane's work in the pipeline.
        Cancelling the caller cancels the lanes too.
        """
        await self.timeline.request_end()
        for lane in self.lanes.values():
            lane.wake()
        tasks = list(self._tasks.values())
        if tasks:
            try:
                _, pending = await asyncio.wait(tasks, timeout=self.finish_timeout_s)
            except asyncio.CancelledError:
                # The job was cancelled (backend shutdown): the lanes go with it. asyncio.wait,
                # unlike gather, doesn't cancel what it waits for.
                await self.cancel()
                raise
            if pending:
                log.warning(
                    "session %s: live lanes %s did not finish in %ss; cancelled",
                    self.ctx.session_id,
                    sorted(name for name, t in self._tasks.items() if t in pending),
                    self.finish_timeout_s,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
        return self.timeline

    async def cancel(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)


class LiveRegistry:
    """The processors of sessions being recorded, by session id. Owned by the job runner."""

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
        """The session's running processor, starting one if needed (at ``POST /live``, or on the
        first chunk after a backend restart). ``None`` when live processing is off."""
        if not self.enabled:
            return None
        processor = self._processors.get(session_id)
        if processor is None:
            processor = LiveProcessor(self._context(session_id), self._lanes)
            self._processors[session_id] = processor
            processor.start()
        return processor

    async def finish(self, session_id: str) -> LiveTimeline | None:
        """Finish and forget the session's processor; ``None`` if it had none."""
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
