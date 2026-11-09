"""Select lane: closed moments -> screen content and figures, while recording.

Reads:  the timeline (closed moments, transcript), frames/
Writes: selection_batches/ (shared cache), visuals/<id>.source.jpg, rendered visuals
        (visuals/<id>.<ext>) and render_cache/, live/selection.json, live/select_batches.json
Publishes: contents, visuals, rejected moment ids and ``selected_to``; emits VISUAL_SELECTED and
        VISUAL_RENDERED with the data the select and render stages emit.

How it works:
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
     ``live_render_concurrency`` at once, by the render stage's rules: an API error gives the
     source crop, unverified and uncached, so the render stage tries the redraw again at stop. A
     render that raises (a blocking error) is logged and leaves no cache entry; the next model
     call (a batch here, or the run at stop) fails on the same error.
  4. ``selected_to`` advances to the end of the last moment read, never past ``closed_to``.
     With every closed moment read, it follows ``closed_to``: that moves on its own while no
     screen is shared (an audio-only recording), and the horizon then follows the transcript.
  5. When ``timeline.upstream_failed``: no more model calls; renders still running are
     cancelled (they leave no cache entry). Wait for stop.
  6. Batches depend on timing, so the lane records each batch's moment ids. A fresh lane (after
     a backend restart) replays them: it waits until all of an earlier batch's moments pass the
     gate and reads them together, so every batch already paid for is a cache hit.
  7. On ``timeline.ending``: wait for the screen lane to finish and the gate to pass (a finished
     audio lane passes it), read the remaining moments, wait for the renders already running,
     and return. Renders not started by then are left to the render stage, which renders every
     figure at stop anyway, with more concurrency (once the live selection is promoted, the
     renders done live are its cache hits).
"""

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
    """live/select_batches.json: the moment ids of each batch read, in order."""

    batches: list[list[str]]


class SelectLane(LiveLane):
    lane = Lane.SELECT

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        self.read = 0  # timeline.moments[:read] have been read
        self._batches: list[list[str]] = []  # read so far (live/select_batches.json)
        self._replay: list[list[str]] = []  # batches an earlier lane read that are still ahead
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

    def _next_batch(self) -> list[Moment]:
        """The moments to read now, or none (wait).

        Normally the gated moments, up to the batch size. A fresh lane replaying what an earlier
        one read waits until the whole of the earlier lane's next batch is gated, so it reads the
        same batch and hits its cache. The replay is dropped as soon as the moments differ or the
        batch can no longer form (dropping it is idempotent, so this is safe in a wait predicate).
        """
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
        """Every closed moment is read, and ``closed_to`` has moved past ``selected_to``."""
        tl = self.timeline
        return self.read == len(tl.moments) and tl.closed_to > tl.selected_to

    def _drained(self) -> bool:
        """Stop was requested, the screen lane will close no more moments and all are read."""
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
                    break  # drained
            if self._halted():
                # Nothing made from here on can be promoted: stop paying for renders too. A
                # render writes its cache entry without awaiting, so cancelling leaves none half
                # written.
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
        # Keep the batches still to replay, so a second restart can rebuild them too.
        record = SelectBatches(batches=self._batches + self._replay)
        write_model(paths.live_select_batches, record)
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
        self.ctx.emit(EventType.VISUAL_RENDERED, render.rendered_event(rendered))


def _load_batches(ctx: StageContext) -> list[list[str]]:
    """The batches an earlier lane of this recording read (before a restart), if any."""
    try:
        return read_model(ctx.paths.live_select_batches, SelectBatches).batches
    except FileNotFoundError:
        return []
    except ValueError:
        log.warning("session %s: ignoring an unreadable %s", ctx.session_id, "select_batches")
        return []
