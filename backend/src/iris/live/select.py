import asyncio
import logging

from pydantic import BaseModel

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.models import EventType, Moment, Stage, Visual
from iris.pipeline.context import StageContext
from iris.pipeline.stages import align, render, select
from iris.store.layout import read_model, write_model

log = logging.getLogger(__name__)


class SelectBatches(BaseModel):
    batches: list[list[str]]


class SelectLane(LiveLane):
    lane = Lane.SELECT

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self.read = 0
        self._batches: list[list[str]] = []
        self._replay: list[list[str]] = []
        self._renders: set[asyncio.Task[None]] = set()
        self._render_limit = asyncio.Semaphore(ctx.settings.live_render_concurrency)

    def _gated(self) -> list[Moment]:
        pad = self.ctx.settings.live_speech_pad_s
        ready = []
        for moment in self.timeline.moments[self.read :]:
            if not self.timeline.transcribed_through(moment.end + pad):
                break
            ready.append(moment)
        return ready

    def _next_batch(self) -> list[Moment]:
        tl = self.timeline
        size = self.ctx.settings.selection_batch_size
        gated = self._gated()
        if self._replay:
            ids = self._replay[0]
            unread = tl.moments[self.read :]
            n = len(ids)
            if (
                n > size
                or [m.id for m in unread[:n]] != ids[: len(unread)]
                or (tl.done(Lane.SCREEN) and len(unread) < n)
            ):
                self._replay = []
            elif len(gated) < n:
                return []
            else:
                return gated[:n]
        return gated[:size]

    def _caught_up(self) -> bool:
        tl = self.timeline
        return self.read == len(tl.moments) and tl.closed_to > tl.selected_to

    def _drained(self) -> bool:
        tl = self.timeline
        return tl.ending and tl.done(Lane.SCREEN) and self.read == len(tl.moments)

    def _halted(self) -> bool:
        return self.timeline.upstream_failed(self.lane)

    async def run(self) -> None:
        tl = self.timeline
        self._replay = _load_batches(self.ctx)
        try:
            while True:
                await tl.wait_until(
                    lambda: (
                        self._halted()
                        or bool(self._next_batch())
                        or self._caught_up()
                        or self._drained()
                    )
                )
                if self._halted():
                    log.warning(
                        "session %s: live select stopped: an upstream lane failed",
                        self.ctx.session_id,
                    )
                    break
                if batch := self._next_batch():
                    await self._read(batch)
                elif self._caught_up():
                    await tl.advance_selected_to(tl.closed_to)
                else:
                    break
            if self._halted():
                for task in self._renders:
                    task.cancel()
                await asyncio.gather(*self._renders, return_exceptions=True)
            elif self._renders:
                await asyncio.gather(*self._renders)
            await tl.wait_until(lambda: tl.ending)
        finally:
            pending = [task for task in self._renders if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def _read(self, moments: list[Moment]) -> None:
        tl = self.timeline
        items = align.align(moments, tl.segments)
        with usage.recording(self.ctx, Stage.SELECT):
            result = await select.read_batch(self.ctx, items, tl.contents, len(tl.visuals) + 1)
        self.read += len(moments)
        ids = [m.id for m in moments]
        self._batches.append(ids)
        if self._replay and self._replay[0] == ids:
            self._replay.pop(0)
        await tl.publish_selection(
            result.contents,
            result.visuals,
            result.rejected_moment_ids,
            selected_to=min(moments[-1].end, tl.closed_to),
        )
        paths = self.ctx.paths
        paths.live_dir.mkdir(parents=True, exist_ok=True)
        write_model(paths.live_selection, tl.selection())
        record = SelectBatches(batches=self._batches + self._replay)
        write_model(paths.live_select_batches, record)
        for visual in result.visuals:
            task = asyncio.create_task(self._render(visual), name=f"live-render-{visual.id}")
            self._renders.add(task)
            task.add_done_callback(self._renders.discard)

    async def _render(self, visual: Visual) -> None:
        async with self._render_limit:
            if self.timeline.ending or self._halted():
                return
            try:
                with usage.recording(self.ctx, Stage.RENDER):
                    rendered = await render.render_visual(self.ctx, visual)
            except Exception:
                log.exception(
                    "session %s: live render of %s failed; the render stage retries it",
                    self.ctx.session_id,
                    visual.id,
                )
                return
        self.ctx.emit(EventType.VISUAL_RENDERED, render.rendered_event(rendered))


def _load_batches(ctx: StageContext) -> list[list[str]]:
    try:
        return read_model(ctx.paths.live_select_batches, SelectBatches).batches
    except FileNotFoundError:
        return []
    except ValueError:
        log.warning("session %s: ignoring an unreadable %s", ctx.session_id, "select_batches")
        return []
