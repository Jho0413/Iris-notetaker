"""Screen lane: captured frames -> moments, while recording.

Reads:  live/capture.json, live/frames/<t_ms>.jpg
Writes: frames/<t_ms>.jpg (the same names and bytes ingest writes), live/frames.json (the
        frames fed so far, rewritten at most every ``INDEX_SAVE_S`` and at stop),
        live/moments.json
Publishes: closed moments (and so ``closed_to``); emits MOMENT_FOUND per moment, with the data
        the detect stage emits.

How it works (docs/architecture/live.md):
  1. Frames are held back so they are fed in time order: the frame at ``t`` is fed once a frame
     at ``t + live_frame_holdback_s`` or later has been captured (the session clock, not the wall
     clock), or at stop. A frame captured earlier than one already fed is too late. If ingest
     would have sampled it, the live frames now differ from ingest's, so it is recorded with
     ``timeline.mark_skipped_input(Lane.SCREEN)``; if its grid cell already has an earlier
     frame, ingest drops it too and nothing is lost.
  2. Frames are sampled onto the ``frame_fps`` grid and normalised by ``GridSampler`` and
     ``FrameNormaliser``, the helpers ``finalize_frames`` uses (size fixed by the first readable
     frame, letterboxed), so ``frames/`` holds what ingest writes at stop.
  3. Each normalised frame is read back from ``frames/`` (as the detect stage reads it) and fed to
     ``MomentDetector`` in a worker thread. Each moment it closes is published in order.
  4. On ``timeline.ending``: the held-back frames are fed, ``detector.finish`` closes the last
     moment at the last frame's time plus one grid step (as ``detect_moments`` does), and the
     lane returns.

An audio-only recording (``live/capture.json``) has no frames and no moments: the lane advances
``closed_to`` with ``transcribed_to`` instead, so the select lane, and so the horizon, follow the
transcript and notes are written while recording.

A fresh lane (after a backend restart) clears ``frames/`` and replays every captured frame, so
its output is the same as if it had run all along.
"""

import asyncio
import logging
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
    read_capture,
)
from iris.store.layout import SessionPaths, write_model

log = logging.getLogger(__name__)

# Frames fed per worker-thread call: a backlog (after a restart) is published as it goes, and
# the lane can be cancelled between calls.
FEED_BATCH = 30
# live/frames.json lists every frame so far, so rewriting it per frame would cost disk writes
# quadratic in the session length. It is rewritten at most this often (session time) and at stop.
INDEX_SAVE_S = 30.0


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
            width, height = self.normaliser.size or (0, 0)
            write_model(
                self.paths.live_frame_index,
                FrameIndex(fps=self.fps, width=width, height=height, frames=self.refs),
            )
        if closed or final:
            write_model(self.paths.live_moments, Moments(moments=self.moments))


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
        if (await asyncio.to_thread(read_capture, paths)).screen:
            await self._feed_frames(feed)
        else:
            await self._follow_transcript()
        await self._publish(await asyncio.to_thread(feed.finish))

    async def _feed_frames(self, feed: ScreenFeed) -> None:
        """Feed captured frames in time order as they arrive, until stop (steps 1 to 3)."""
        holdback_ms = round(self.ctx.settings.live_frame_holdback_s * 1000)
        paths = self.ctx.paths
        seen: set[int] = set()  # captured frames already handled, by time in ms
        held: list[tuple[int, Path]] = []  # captured but not fed yet
        latest_ms = -1
        while True:
            # Read before listing, so every frame stored before stop was requested is fed.
            ending = self.timeline.ending
            for t, path in await asyncio.to_thread(captured_frames, paths):
                ms = round(t * 1000)
                if ms in seen:
                    continue
                seen.add(ms)
                latest_ms = max(latest_ms, ms)
                if feed.fed_t is not None and t < feed.fed_t:
                    await self._too_late(feed, t)
                else:
                    held.append((ms, path))
            held.sort()
            if ending:
                ready, held = held, []
            else:
                split = next(
                    (i for i, (ms, _) in enumerate(held) if ms + holdback_ms > latest_ms), len(held)
                )
                ready, held = held[:split], held[split:]
            for chunk in batched(ready, FEED_BATCH):
                frames = [(ms / 1000, path) for ms, path in chunk]
                await self._publish(await asyncio.to_thread(feed.feed, frames))
            if ending:
                break
            await self.wait_for_input()

    async def _follow_transcript(self) -> None:
        """No screen is shared, so no moment can open: every moment that ends by the latest time
        the recording is known up to (``transcribed_to``) is closed. Until stop."""
        tl = self.timeline
        while True:
            await tl.wait_until(lambda: tl.ending or tl.transcribed_to > tl.closed_to)
            if tl.ending:
                return
            await tl.advance_closed_to(tl.transcribed_to)

    async def _too_late(self, feed: ScreenFeed, t: float) -> None:
        if feed.would_sample(t):
            log.warning(
                "session %s: frame at %.3fs arrived after later frames were processed; "
                "skipped live",
                self.ctx.session_id,
                t,
            )
            await self.timeline.mark_skipped_input(self.lane)
        else:
            log.debug("session %s: late frame at %.3fs is not sampled", self.ctx.session_id, t)

    async def _publish(self, moments: list[Moment]) -> None:
        for moment in moments:
            await self.timeline.publish_moment(moment)
            self.ctx.emit(EventType.MOMENT_FOUND, moment_event(moment))
