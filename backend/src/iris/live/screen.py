import asyncio
import logging
from itertools import batched

import cv2

from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.media.frames import FrameNormaliser, GridSampler, clear_frames
from iris.models import EventType, FrameRef, Moment
from iris.pipeline.context import StageContext
from iris.pipeline.stages.detect import DetectParams, MomentDetector, moment_event, share_end
from iris.sources.browser_live import CapturedFrame, captured_frames
from iris.store.layout import SessionPaths

log = logging.getLogger(__name__)

FEED_BATCH = 30


class ScreenFeed:
    def __init__(self, paths: SessionPaths, fps: float, max_width: int, params: DetectParams):
        self.paths = paths
        self.fps = fps
        self.sampler = GridSampler(fps)
        self.normaliser = FrameNormaliser(paths.frames_dir, max_width)
        self.detector = MomentDetector(params)
        self.latest_fed: tuple[float, int] | None = None
        self.latest_share: int | None = None
        self.shares_seen = 0
        self._share_of_latest_ref = 0
        self.last_ref: FrameRef | None = None
        self._sampled_in_cell: dict[int, tuple[float, int]] = {}

    def would_sample(self, frame: CapturedFrame) -> bool:
        picked = self._sampled_in_cell.get(self.sampler.cell(frame.t))
        return picked is None or picked > (frame.t, frame.share)

    def feed(self, frames: list[CapturedFrame]) -> list[Moment]:
        closed: list[Moment] = []
        for frame in frames:
            self.latest_fed = (frame.t, frame.share)
            if not self.sampler.take(frame.t):
                continue
            self._sampled_in_cell[self.sampler.cell(frame.t)] = self.latest_fed
            if frame.share != self.latest_share:
                self.latest_share = frame.share
                self.shares_seen += 1
                self.normaliser.new_share()
            ref = self.normaliser.add(frame.t, frame.path)
            if ref is None:
                continue
            if self.last_ref and self._share_of_latest_ref != self.shares_seen:
                end = share_end(self.last_ref.t, self.fps, ref.t)
                if (moment := self.detector.end_share(end)) is not None:
                    closed.append(moment)
            self._share_of_latest_ref = self.shares_seen
            self.last_ref = ref
            image = cv2.imread(str(self.paths.root / ref.path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            if (moment := self.detector.feed(ref.t, ref, image)) is not None:
                closed.append(moment)
        return closed

    def finish(self) -> list[Moment]:
        closed = []
        if self.last_ref and (moment := self.detector.finish(share_end(self.last_ref.t, self.fps))):
            closed.append(moment)
        return closed


def _lateness(feed: ScreenFeed, frame: CapturedFrame, closed_to: float) -> str | None:
    if feed.latest_fed is not None and (frame.t, frame.share) < feed.latest_fed:
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
        tl = self.timeline
        holdback_ms = round(self.ctx.settings.live_frame_holdback_s * 1000)
        handled: set[tuple[float, int]] = set()
        held: list[CapturedFrame] = []
        latest_captured_ms = -1
        while True:
            ending_before_listing = tl.ending
            transcribed_to = tl.transcribed_to
            for frame in await asyncio.to_thread(captured_frames, self.ctx.paths):
                if (frame.t, frame.share) in handled:
                    continue
                handled.add((frame.t, frame.share))
                latest_captured_ms = max(latest_captured_ms, round(frame.t * 1000))
                if reason := _lateness(feed, frame, tl.closed_to):
                    await self._too_late(feed, frame, reason)
                else:
                    held.append(frame)
            held.sort()
            if ending_before_listing:
                ready, held = held, []
            else:
                known_ms = max(latest_captured_ms, round(transcribed_to * 1000))
                split = next(
                    (i for i, f in enumerate(held) if round(f.t * 1000) + holdback_ms > known_ms),
                    len(held),
                )
                ready, held = held[:split], held[split:]
            for chunk in batched(ready, FEED_BATCH):
                await self._publish(await asyncio.to_thread(feed.feed, list(chunk)))
            if ending_before_listing:
                break
            if feed.latest_fed is None:
                await self._follow_transcript(held, transcribed_to)
            await self._wait(transcribed_to)

    async def _follow_transcript(self, held: list[CapturedFrame], transcribed_to: float) -> None:
        until = min([transcribed_to, *(frame.t for frame in held)])
        if until > self.timeline.closed_to:
            await self.timeline.advance_closed_to(until)

    async def _wait(self, transcribed_to: float) -> None:
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
