"""Select lane: closed moments -> screen content and figures, while recording.

Reads:  the timeline (closed moments, transcript), frames/
Writes: selection_batches/ (shared cache), visuals/<id>.source.jpg, rendered visuals
        (visuals/<id>.<ext>) and render_cache/, live/selection.json
Publishes: contents, visuals, rejected moment ids and ``selected_to``; emits VISUAL_SELECTED and
        VISUAL_RENDERED with the data the select and render stages emit.

How it works (architecture section 1, "Life of a session: live"):
  1. A closed moment is gated until ``timeline.transcribed_through(moment.end +
     live_speech_pad_s)``, so speech that outlasts the screen is part of it, then aligned
     (``align.align``) with the transcript so far. Moments close in time order and the gate is
     monotonic, so the moments that pass it are always the next ones in order.
  2. Moments are read with the select stage's own batch step (``select.read_batch``): one call in
     flight; the moments that passed the gate meanwhile go together in the next batch (up to
     ``selection_batch_size``), with earlier moments' content as context. Visual ids continue the
     pipeline's numbering. A batch made of the same moments, after the same content, as one of
     the select stage's has the same cache key, so stop reuses it.
  3. Each new figure renders in the background (``render.render_visual``), at most
     ``live_render_concurrency`` at once. A failed render is logged and leaves no cache entry, so
     the render stage retries it at stop.
  4. ``selected_to`` advances to the end of the last moment read, never past ``closed_to``.
  5. When ``timeline.upstream_failed``: no more model calls; renders still running are
     cancelled (they leave no cache entry). Wait for stop.
  6. Batches depend on timing, so a fresh lane (after a backend restart) replaying a backlog
     first looks for the longest run of gated moments that is already a cached batch
     (``select.cached_prefix``, local work) and reads that, so nothing is paid for twice.
  7. On ``timeline.ending``: wait for the screen lane to finish and the gate to pass (a finished
     audio lane passes it), read the remaining moments, wait for the renders already running,
     and return. Renders not started by then are left to the render stage, which renders every
     figure at stop anyway (hitting the cache for those done live), with more concurrency.
"""

import asyncio
import logging

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.models import EventType, Moment, Stage, Visual
from iris.pipeline.context import StageContext
from iris.pipeline.stages import align, render, select
from iris.store.layout import write_model

log = logging.getLogger(__name__)


class SelectLane(LiveLane):
    lane = Lane.SELECT

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self.read = 0  # timeline.moments[:read] have been read
        self._renders: set[asyncio.Task[None]] = set()
        self._render_limit = asyncio.Semaphore(ctx.settings.live_render_concurrency)

    def _gated(self) -> list[Moment]:
        """The unread moments whose speech is transcribed, in order."""
        pad = self.ctx.settings.live_speech_pad_s
        ready = []
        for moment in self.timeline.moments[self.read :]:
            if not self.timeline.transcribed_through(moment.end + pad):
                break
            ready.append(moment)
        return ready

    def _drained(self) -> bool:
        """Stop was requested, the screen lane will close no more moments and all are read."""
        tl = self.timeline
        return tl.ending and tl.done(Lane.SCREEN) and self.read == len(tl.moments)

    def _halted(self) -> bool:
        return self.timeline.upstream_failed(self.lane)

    async def run(self) -> None:
        tl = self.timeline
        size = self.ctx.settings.selection_batch_size
        try:
            while True:
                await tl.wait_until(
                    lambda: self._halted() or bool(self._gated()) or self._drained()
                )
                if self._halted():
                    log.warning(
                        "session %s: live select stopped: an upstream lane failed",
                        self.ctx.session_id,
                    )
                    break
                batch = self._gated()[:size]
                if not batch:
                    break  # drained
                await self._read(batch)
            if self._halted():
                # Nothing made from here on can be promoted: stop paying for renders too. A
                # render writes its cache entry without awaiting, so cancelling leaves none half
                # written.
                for task in self._renders:
                    task.cancel()
            if self._renders:
                await asyncio.gather(*self._renders, return_exceptions=True)
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
        if len(items) > 1:
            # After a restart, read the backlog in the batches already paid for.
            cached = await asyncio.to_thread(select.cached_prefix, self.ctx, items, tl.contents)
            if cached:
                moments, items = moments[:cached], items[:cached]
        with usage.recording(self.ctx, Stage.SELECT):
            result = await select.read_batch(self.ctx, items, tl.contents, len(tl.visuals) + 1)
        self.read += len(moments)
        await tl.publish_selection(
            result.contents,
            result.visuals,
            result.rejected_moment_ids,
            selected_to=min(moments[-1].end, tl.closed_to),
        )
        self.ctx.paths.live_dir.mkdir(parents=True, exist_ok=True)
        write_model(self.ctx.paths.live_selection, tl.selection())
        for visual in result.visuals:
            task = asyncio.create_task(self._render(visual), name=f"live-render-{visual.id}")
            self._renders.add(task)
            task.add_done_callback(self._renders.discard)

    async def _render(self, visual: Visual) -> None:
        async with self._render_limit:
            if self.timeline.ending or self._halted():
                return  # the render stage renders it at stop, if it runs
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
        self.ctx.emit(EventType.VISUAL_RENDERED, rendered.model_dump(mode="json"))
