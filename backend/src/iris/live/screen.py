"""Screen lane: captured frames -> moments, while recording.

Reads:  live/frames/<t_ms>.jpg, live/screen/<t_ms>.on|off
Writes: frames/<t_ms>.jpg (the same names and bytes ingest writes), live/frames.json (the
        frames fed so far, rewritten at most every ``INDEX_SAVE_S`` and at stop),
        live/moments.json
Publishes: closed moments (and so ``closed_to``); emits MOMENT_FOUND per moment, with the data
        the detect stage emits.

How it works (docs/architecture/live.md):
  1. Frames and the times the screen was turned off are held back so they are handled in time
     order: one at ``t`` is handled once the recording is known to have reached
     ``t + live_frame_holdback_s`` (session time: the latest frame or screen change captured, or
     ``transcribed_to`` while no frames come), or at stop. Input that can no longer be handled
     in that order is too late (``_lateness``): a frame earlier than one already fed or than an
     off already applied, an off at or before a frame already fed, or anything before
     ``closed_to``. If ingest would have used it, the live moments now
     differ from ingest's, so it is recorded with ``timeline.mark_skipped_input(Lane.SCREEN)``;
     a late frame whose grid cell already has an earlier frame is dropped by ingest too, and
     nothing is lost.
  2. Frames are sampled onto the ``frame_fps`` grid and normalised by ``GridSampler`` and
     ``FrameNormaliser``, the helpers ``finalize_frames`` uses (size fixed by each share's first
     readable frame, letterboxed), so ``frames/`` holds what ingest writes at stop.
  3. Each normalised frame is read back from ``frames/`` (as the detect stage reads it) and fed to
     ``MomentDetector`` in a worker thread. At an off time, ``detector.screen_off`` closes the
     open moment there, as ``detect_moments`` does. Each moment closed is published in order.
  4. While the screen is off (before the first frame, or after an off and before the next
     frame), no moment is open, and none can start before the next frame or the next time the
     screen is turned on: ``closed_to`` follows ``transcribed_to`` up to there, so the select
     lane, and so the horizon, follow the transcript and notes are written while nothing is
     shared. An audio-only recording is off throughout.
  5. On ``timeline.ending``: everything held is handled, ``detector.finish`` closes the last
     moment at the last frame's time plus one grid step (as ``detect_moments`` does), and the
     lane returns.

A fresh lane (after a backend restart) clears ``frames/`` and replays every captured frame, so
its output is the same as if it had run all along.
"""

import asyncio
import logging
from enum import IntEnum
from itertools import batched
from pathlib import Path

import cv2

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.models import EventType, FrameIndex, FrameRef, Moment, Moments
from iris.pipeline.context import StageContext
from iris.pipeline.stages.detect import DetectParams, MomentDetector, moment_event
from iris.sources.browser_live import (
    FrameNormaliser,
    GridSampler,
    captured_frames,
    clear_frames,
    screen_changes,
)
from iris.store.layout import SessionPaths, write_model

log = logging.getLogger(__name__)

# Frames fed per worker-thread call: a backlog (after a restart) is published as it goes, and
# the lane can be cancelled between calls.
FEED_BATCH = 30
# live/frames.json lists every frame so far, so rewriting it per frame would cost disk writes
# quadratic in the session length. It is rewritten at most this often (session time) and at stop.
INDEX_SAVE_S = 30.0


class Change(IntEnum):
    """What was captured at a time. At the same time an off comes first: it ends the share
    before, and a frame then starts the next (as ingest splits them)."""

    OFF = 0
    FRAME = 1
    ON = 2


def _captured(paths: SessionPaths) -> list[tuple[int, Change, Path | None]]:
    """Captured frames and screen changes as ``(t_ms, change, frame path)`` (blocking)."""
    found: list[tuple[int, Change, Path | None]] = [
        (round(t * 1000), Change.FRAME, path) for t, path in captured_frames(paths)
    ]
    found += [
        (round(t * 1000), Change.ON if on else Change.OFF, None) for t, on in screen_changes(paths)
    ]
    return found


class ScreenFeed:
    """Sampling, normalising and detection state of one recording (blocking; call in a thread).

    Frames are fed in time order. Not thread-safe: one call at a time.
    """

    def __init__(self, paths: SessionPaths, fps: float, max_width: int, params: DetectParams):
        self.paths = paths
        self.fps = fps
        self.sampler = GridSampler(fps)
        self.normaliser = FrameNormaliser(paths.frames_dir, max_width)
        self.detector = MomentDetector(params)
        self.fed_t: float | None = None  # the latest frame fed
        self.sharing = False  # a frame was fed since the start or the last time the screen was off
        self.off_t: float | None = None  # the latest time the screen was turned off
        self.offs: list[float] = []
        self.refs: list[FrameRef] = []
        self.moments: list[Moment] = []
        self._picked: dict[int, float] = {}  # grid cell -> the frame sampled in it
        self._index_saved_t: float | None = None  # the latest frame in live/frames.json

    def would_sample(self, t: float) -> bool:
        """Whether ingest samples a frame at ``t`` earlier than ``fed_t`` that was not fed:
        true unless its grid cell already holds an earlier frame."""
        picked = self._picked.get(self.sampler.cell(t))
        return picked is None or picked > t

    def feed(self, frames: list[tuple[float, Path]]) -> list[Moment]:
        """Feed frames (in time order, all later than ``fed_t``); return the moments closed."""
        closed: list[Moment] = []
        for t, src in frames:
            self.fed_t = t
            self.sharing = True
            if not self.sampler.take(t):
                continue
            self._picked[self.sampler.cell(t)] = t
            ref = self.normaliser.add(t, src)
            if ref is None:
                continue
            self.refs.append(ref)
            # Read what ingest wrote, as the detect stage does, so moments match it exactly.
            image = cv2.imread(str(self.paths.root / ref.path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            if (moment := self.detector.feed(ref.t, ref, image)) is not None:
                closed.append(moment)
        saved = self._index_saved_t
        due = bool(self.refs) and (saved is None or self.refs[-1].t - saved >= INDEX_SAVE_S)
        self._save(closed, index=due)
        return closed

    def screen_off(self, t: float) -> list[Moment]:
        """The screen was turned off at ``t`` (after every frame fed): close what is open."""
        closed = [m] if (m := self.detector.screen_off(t)) is not None else []
        self.normaliser.new_share()
        self.sharing = False
        self.off_t = t
        self.offs.append(t)
        self._save(closed, index=True)
        return closed

    def finish(self) -> list[Moment]:
        """Close the last moment, as ``detect_moments`` does at the end of the frames."""
        closed = []
        if self.refs:
            step = 1.0 / self.fps if self.fps > 0 else 0.0
            if (moment := self.detector.finish(self.refs[-1].t + step)) is not None:
                closed.append(moment)
        self._save(closed, final=True)
        return closed

    def _save(self, closed: list[Moment], *, index: bool = False, final: bool = False) -> None:
        """Write what changed; at the end, write both (an empty index if there were no frames)."""
        self.moments.extend(closed)
        self.paths.live_dir.mkdir(parents=True, exist_ok=True)
        if index or final:
            self._index_saved_t = self.refs[-1].t if self.refs else None
            width, height = self.normaliser.first_size or (0, 0)
            frame_index = FrameIndex(
                fps=self.fps, width=width, height=height, frames=self.refs, screen_off=self.offs
            )
            write_model(self.paths.live_frame_index, frame_index)
        if closed or final:
            write_model(self.paths.live_moments, Moments(moments=self.moments))


def _lateness(feed: ScreenFeed, t: float, change: Change, closed_to: float) -> str | None:
    """Why a frame or screen-off captured at ``t`` can no longer be handled in time order (as
    ingest and detect order them), or None if it still can. Ingest puts a frame at an off time in
    the next share, so an off at a frame already fed is too late too."""
    if feed.fed_t is not None and (t <= feed.fed_t if change == Change.OFF else t < feed.fed_t):
        return "after later frames were processed"
    if change == Change.FRAME and feed.off_t is not None and t < feed.off_t:
        return "after the screen was turned off after it"
    if t < closed_to:
        return "after the moments up to then were closed"
    return None


class ScreenLane(LiveLane):
    lane = Lane.SCREEN

    def __init__(
        self, ctx: StageContext, timeline: LiveTimeline, params: DetectParams | None = None
    ):
        super().__init__(ctx, timeline)
        self.params = params or DetectParams()

    async def run(self) -> None:
        s = self.ctx.settings
        paths = self.ctx.paths
        feed = ScreenFeed(paths, s.frame_fps, s.frame_max_width, self.params)
        await asyncio.to_thread(clear_frames, paths)
        await self._follow(feed)
        await self._publish(await asyncio.to_thread(feed.finish))

    async def _follow(self, feed: ScreenFeed) -> None:
        """Handle frames and screen-offs in time order as they arrive, and move ``closed_to``
        while the screen is off, until stop (steps 1 to 4)."""
        tl = self.timeline
        holdback_ms = round(self.ctx.settings.live_frame_holdback_s * 1000)
        seen: set[tuple[int, Change]] = set()  # captured input already handled
        held: list[tuple[int, Change, Path | None]] = []  # captured, not handled yet
        ons: list[int] = []  # when the screen was turned on, ms
        latest_ms = -1  # the latest frame or screen change captured
        while True:
            # Read before listing, so everything stored before stop was requested is handled.
            ending = tl.ending
            transcribed_to = tl.transcribed_to
            arrived = await asyncio.to_thread(_captured, self.ctx.paths)
            for ms, change, path in arrived:
                if (ms, change) in seen:
                    continue
                seen.add((ms, change))
                latest_ms = max(latest_ms, ms)
                if change == Change.ON:
                    ons.append(ms)
                elif reason := _lateness(feed, ms / 1000, change, tl.closed_to):
                    await self._too_late(feed, ms / 1000, change, reason)
                else:
                    held.append((ms, change, path))
            held.sort(key=lambda item: (item[0], item[1]))
            if ending:
                ready, held = held, []
            else:
                known_ms = max(latest_ms, round(transcribed_to * 1000))
                split = next(
                    (i for i, (ms, _, _) in enumerate(held) if ms + holdback_ms > known_ms),
                    len(held),
                )
                ready, held = held[:split], held[split:]
            await self._handle(feed, ready)
            if ending:
                break
            if not feed.sharing:
                await self._follow_transcript(feed, held, ons, transcribed_to)
            await self._wait(transcribed_to)

    async def _handle(self, feed: ScreenFeed, ready: list[tuple[int, Change, Path | None]]) -> None:
        """Feed ready frames in batches and apply ready offs, in time order."""
        frames: list[tuple[float, Path]] = []

        async def flush() -> None:
            for chunk in batched(frames, FEED_BATCH):
                await self._publish(await asyncio.to_thread(feed.feed, list(chunk)))
            frames.clear()

        for ms, change, path in ready:
            if change == Change.FRAME:
                assert path is not None
                frames.append((ms / 1000, path))
                continue
            await flush()
            await self._publish(await asyncio.to_thread(feed.screen_off, ms / 1000))
        await flush()

    async def _follow_transcript(
        self,
        feed: ScreenFeed,
        held: list[tuple[int, Change, Path | None]],
        ons: list[int],
        transcribed_to: float,
    ) -> None:
        """Nothing is shared now, so no moment is open, and none can start before the next frame
        or the next time the screen was turned on: every moment that ends by then, or by the
        latest time the recording is known up to (``transcribed_to``), is closed."""
        since_ms = round(feed.off_t * 1000) if feed.off_t is not None else 0
        upcoming = [ms for ms, change, _ in held if change == Change.FRAME]
        upcoming += [ms for ms in ons if ms >= since_ms]
        until = min([transcribed_to, *(ms / 1000 for ms in upcoming)])
        if until > self.timeline.closed_to:
            await self.timeline.advance_closed_to(until)

    async def _wait(self, transcribed_to: float) -> None:
        """Sleep until new input arrives, the transcript moves on (the clock that releases what
        is held while no frames come) or stop is requested."""
        tl = self.timeline
        waits = [
            asyncio.ensure_future(self.wait_for_input()),
            asyncio.ensure_future(
                tl.wait_until(lambda: tl.ending or tl.transcribed_to > transcribed_to)
            ),
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for wait in waits:
                wait.cancel()

    async def _too_late(self, feed: ScreenFeed, t: float, change: Change, reason: str) -> None:
        if change == Change.OFF or feed.would_sample(t):
            what = "frame" if change == Change.FRAME else "screen off"
            log.warning(
                "session %s: %s at %.3fs arrived %s; skipped live",
                self.ctx.session_id,
                what,
                t,
                reason,
            )
            await self.timeline.mark_skipped_input(self.lane)
        else:
            log.debug("session %s: late frame at %.3fs is not sampled", self.ctx.session_id, t)

    async def _publish(self, moments: list[Moment]) -> None:
        for moment in moments:
            await self.timeline.publish_moment(moment)
            self.ctx.emit(EventType.MOMENT_FOUND, moment_event(moment))
