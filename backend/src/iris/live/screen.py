"""Screen lane: captured frames -> moments, while recording.

Reads:  live/frames/<t_ms>-<share>.jpg
Writes: frames/<t_ms>.jpg (the same names and bytes ingest writes), live/frames.json (the
        frames fed so far, rewritten at most every ``INDEX_SAVE_S`` and when notes are made),
        live/moments.json
Publishes: closed moments (and so ``closed_to``); emits MOMENT_FOUND per moment, with the data
        the detect stage emits.

How it works:
  1. Frames are held back so they are handled in time order: one at ``t`` is handled once the
     recording is known to have reached ``t + live_frame_holdback_s`` (session time: the latest
     frame captured, or ``transcribed_to`` while no frames come), or when notes are made. A frame
     that can no longer be handled in that order is too late (``_lateness``): one earlier than a
     frame already fed, or before ``closed_to``. If ingest would have used it, the live moments now
     differ from ingest's, so it is recorded with ``timeline.mark_skipped_input(Lane.SCREEN)``; a
     late frame whose grid cell already has an earlier frame is dropped by ingest too, and nothing
     is lost.
  2. Frames are sampled onto the ``frame_fps`` grid and normalised by ``GridSampler`` and
     ``FrameNormaliser``, the helpers ``finalize_frames`` uses (size fixed by each share's first
     readable frame, letterboxed), so ``frames/`` holds what ingest writes when notes are made.
  3. Each normalised frame is read back from ``frames/`` (as the detect stage reads it) and fed to
     ``MomentDetector`` in a worker thread. Where the share number changes, ``detector.end_share``
     closes the open moment where the share ended (``share_end``), as ``detect_moments`` does.
     Each moment closed is published in order.
  4. Until the first frame comes (an audio-only recording throughout, or Record audio before the
     screen is shared), no moment is open, and none can start before the first frame:
     ``closed_to`` follows ``transcribed_to`` up to there, so the select lane, and so the horizon,
     follow the transcript and notes are written while nothing is shared. Once a share has
     started, a moment stays open until the next share's frames or making notes say where it
     ended: nothing else can tell sharing stopping from frames still on their way.
  5. On ``timeline.ending``: everything held is handled, ``detector.finish`` closes the last
     moment one grid step after the last frame (as ``detect_moments`` does), and the lane
     returns.

A fresh lane (after a backend restart) clears ``frames/`` and replays every captured frame, so
its output is the same as if it had run all along.
"""

import asyncio
import logging
from itertools import batched

import cv2

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.models import EventType, FrameIndex, FrameRef, Moment, Moments
from iris.pipeline.context import StageContext
from iris.pipeline.stages.detect import DetectParams, MomentDetector, moment_event, share_end
from iris.sources.browser_live import (
    CapturedFrame,
    FrameNormaliser,
    GridSampler,
    captured_frames,
    clear_frames,
)
from iris.store.layout import SessionPaths, write_model

log = logging.getLogger(__name__)

# Frames fed per worker-thread call: a backlog (after a restart) is published as it goes, and
# the lane can be cancelled between calls.
FEED_BATCH = 30
# live/frames.json lists every frame so far, so rewriting it per frame would cost disk writes
# quadratic in the session length. It is rewritten at most this often (session time) and when
# notes are made.
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
        self.fed: tuple[float, int] | None = None  # (t, share) of the latest frame fed
        self.share: int | None = None  # the share number of the latest frame sampled
        self.shares = 0  # how many times the share number changed (ingest's shares, counted)
        self._ref_share = 0  # which of those the latest frame written belongs to
        self.refs: list[FrameRef] = []
        self.starts: list[float] = []  # FrameIndex.share_starts
        self.moments: list[Moment] = []
        self._picked: dict[int, tuple[float, int]] = {}  # grid cell -> the frame sampled in it
        self._index_saved_t: float | None = None  # the latest frame in live/frames.json

    def would_sample(self, frame: CapturedFrame) -> bool:
        """Whether ingest samples a frame earlier than ``fed`` that was not fed: true unless its
        grid cell already holds an earlier frame."""
        picked = self._picked.get(self.sampler.cell(frame.t))
        return picked is None or picked > (frame.t, frame.share)

    def feed(self, frames: list[CapturedFrame]) -> list[Moment]:
        """Feed frames (in time order, all later than ``fed``); return the moments closed."""
        closed: list[Moment] = []
        for frame in frames:
            self.fed = (frame.t, frame.share)
            if not self.sampler.take(frame.t):
                continue
            self._picked[self.sampler.cell(frame.t)] = self.fed
            if frame.share != self.share:
                self.share = frame.share
                self.shares += 1
                self.normaliser.new_share()
            ref = self.normaliser.add(frame.t, frame.path)
            if ref is None:
                continue
            if self.refs and self._ref_share != self.shares:
                # The first frame of the next share: the share before it ended.
                end = share_end(self.refs[-1].t, self.fps, ref.t)
                if (moment := self.detector.end_share(end)) is not None:
                    closed.append(moment)
                self.starts.append(ref.t)
            self._ref_share = self.shares
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
        if self.refs and (moment := self.detector.finish(share_end(self.refs[-1].t, self.fps))):
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
                fps=self.fps,
                width=width,
                height=height,
                frames=self.refs,
                share_starts=self.starts,
            )
            write_model(self.paths.live_frame_index, frame_index)
        if closed or final:
            write_model(self.paths.live_moments, Moments(moments=self.moments))


def _lateness(feed: ScreenFeed, frame: CapturedFrame, closed_to: float) -> str | None:
    """Why a frame can no longer be handled in time order (as ingest and detect order them), or
    None if it still can."""
    if feed.fed is not None and (frame.t, frame.share) < feed.fed:
        return "after later frames were processed"
    if frame.t < closed_to:
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
        """Handle frames in time order as they arrive, and move ``closed_to`` until the first
        frame, until notes are asked for (steps 1 to 4)."""
        tl = self.timeline
        holdback_ms = round(self.ctx.settings.live_frame_holdback_s * 1000)
        seen: set[tuple[float, int]] = set()  # captured frames already handled
        held: list[CapturedFrame] = []  # captured, not handled yet
        latest_ms = -1  # the latest frame captured
        while True:
            # Read before listing, so everything stored before notes were asked for is handled.
            ending = tl.ending
            transcribed_to = tl.transcribed_to
            for frame in await asyncio.to_thread(captured_frames, self.ctx.paths):
                if (frame.t, frame.share) in seen:
                    continue
                seen.add((frame.t, frame.share))
                latest_ms = max(latest_ms, round(frame.t * 1000))
                if reason := _lateness(feed, frame, tl.closed_to):
                    await self._too_late(feed, frame, reason)
                else:
                    held.append(frame)
            held.sort()
            if ending:
                ready, held = held, []
            else:
                known_ms = max(latest_ms, round(transcribed_to * 1000))
                split = next(
                    (i for i, f in enumerate(held) if round(f.t * 1000) + holdback_ms > known_ms),
                    len(held),
                )
                ready, held = held[:split], held[split:]
            for chunk in batched(ready, FEED_BATCH):
                await self._publish(await asyncio.to_thread(feed.feed, list(chunk)))
            if ending:
                break
            if feed.fed is None:
                await self._follow_transcript(held, transcribed_to)
            await self._wait(transcribed_to)

    async def _follow_transcript(self, held: list[CapturedFrame], transcribed_to: float) -> None:
        """Nothing has been shared yet, so no moment is open, and none can start before the first
        frame: every moment that ends by then, or by the latest time the recording is known up
        to (``transcribed_to``), is closed."""
        until = min([transcribed_to, *(frame.t for frame in held)])
        if until > self.timeline.closed_to:
            await self.timeline.advance_closed_to(until)

    async def _wait(self, transcribed_to: float) -> None:
        """Sleep until new input arrives, the transcript moves on (the clock that releases what
        is held while no frames come) or notes are asked for."""
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

    async def _too_late(self, feed: ScreenFeed, frame: CapturedFrame, reason: str) -> None:
        if feed.would_sample(frame):
            log.warning(
                "session %s: frame at %.3fs (share %d) arrived %s; skipped live",
                self.ctx.session_id,
                frame.t,
                frame.share,
                reason,
            )
            await self.timeline.mark_skipped_input(self.lane)
        else:
            log.debug(
                "session %s: late frame at %.3fs is not sampled", self.ctx.session_id, frame.t
            )

    async def _publish(self, moments: list[Moment]) -> None:
        for moment in moments:
            await self.timeline.publish_moment(moment)
            self.ctx.emit(EventType.MOMENT_FOUND, moment_event(moment))
