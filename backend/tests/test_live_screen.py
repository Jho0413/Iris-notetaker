"""Live screen lane: captured frames -> normalised frames and moments, while recording.

Frames are synthetic JPEGs stored as the API stores them; the lane is driven one delivery at a
time (store frames, wake the lane, wait until it is idle again), so tests are deterministic.
"""

import asyncio
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from iris.config import Settings
from iris.live.screen import ScreenLane
from iris.live.timeline import Lane, LiveTimeline
from iris.models import EventType, FrameIndex, LiveCapture, Moments, SourceKind
from iris.pipeline.context import StageContext
from iris.pipeline.stages.detect import detect_moments, moment_event
from iris.sources.browser_live import finalize_frames, read_capture, store_capture, store_frame
from iris.store.layout import SessionPaths, read_model
from tests.test_detect import SLIDE_A, SLIDE_B, Deck, three_slides
from tests.test_live import COLOURS, jpeg


@pytest.fixture
def events() -> list[tuple[EventType, dict]]:
    return []


def make_ctx(tmp_path: Path, events: list, **settings) -> StageContext:
    return StageContext(
        session_id="s1",
        source=SourceKind.BROWSER_LIVE,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path, **settings),
        emit=lambda kind, data: events.append((kind, data)),
    )


@pytest.fixture
def ctx(tmp_path: Path, events: list) -> StageContext:
    return make_ctx(tmp_path, events, live_frame_holdback_s=2.0, frame_max_width=320)


class Driver:
    """Runs a screen lane and hands it frames one delivery at a time."""

    def __init__(self, ctx: StageContext):
        self.ctx = ctx
        self.timeline = LiveTimeline()
        self.lane = ScreenLane(ctx, self.timeline)
        self.idle = asyncio.Event()
        wait = self.lane.wait_for_input

        async def waiting() -> None:
            self.idle.set()  # the lane has handled everything delivered so far
            await wait()

        self.lane.wait_for_input = waiting  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> "Driver":
        self.task = asyncio.create_task(self.lane.run())
        await asyncio.wait_for(self.idle.wait(), 10)
        return self

    async def deliver(self, *frames: tuple[float, bytes]) -> None:
        for t, data in frames:
            store_frame(self.ctx.paths, t, data)
        self.idle.clear()
        self.lane.wake()
        await asyncio.wait_for(self.idle.wait(), 10)

    async def stop(self) -> LiveTimeline:
        await self.timeline.request_end()
        self.lane.wake()
        assert self.task is not None
        await asyncio.wait_for(self.task, 10)
        return self.timeline


def encode(image: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".jpg", image)
    assert ok
    return buf.tobytes()


def frame_files(paths: SessionPaths) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(paths.frames_dir.glob("*.jpg"))}


# --- Ordering and normalising -----------------------------------------------------------------


async def test_shuffled_frames_within_the_holdback_match_ingest(ctx: StageContext):
    frames = {
        0.0: b"\xff\xd8\xff truncated",  # unreadable: the size comes from the next frame
        1.02: jpeg(COLOURS[1], (640, 480)),  # first readable: fixes the size at 320x240
        1.6: jpeg(COLOURS[2], (640, 480)),  # same grid cell as 1.02: not sampled
        2.05: jpeg(COLOURS[3], (640, 480)),
        3.5: jpeg(COLOURS[4], (320, 240)),  # already the right size: copied
        4.3: jpeg(COLOURS[5], (640, 480)),
        5.0: jpeg(COLOURS[0], (640, 240)),  # the shared window got wider: letterboxed
        6.1: jpeg(COLOURS[1], (640, 480)),
        7.0: jpeg(COLOURS[2], (640, 480)),
    }
    # Each delivery is out of order, but no frame comes 2 s after a later one.
    deliveries = [[1.02, 0.0], [2.05, 1.6], [4.3], [3.5], [6.1, 5.0], [7.0]]
    driver = await Driver(ctx).start()
    latest = 0.0
    for times in deliveries:
        await driver.deliver(*((t, frames[t]) for t in times))
        latest = max(latest, *times)
        if ctx.paths.live_frame_index.exists():  # fed only once a frame 2 s later exists
            live = read_model(ctx.paths.live_frame_index, FrameIndex)
            assert max(f.t for f in live.frames) + 2.0 <= latest + 1e-9
    timeline = await driver.stop()

    live_index = read_model(ctx.paths.live_frame_index, FrameIndex)
    live_files = frame_files(ctx.paths)
    ingest_index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert live_index == ingest_index
    assert [f.t for f in live_index.frames] == [1.02, 2.05, 3.5, 4.3, 5.0, 6.1, 7.0]
    assert (live_index.width, live_index.height) == (320, 240)
    assert live_files == frame_files(ctx.paths)
    assert not timeline.skipped_input


@pytest.mark.parametrize(
    ("late", "recorded"),
    [
        (1.5, False),  # its grid cell already holds an earlier frame: ingest drops it too
        (3.0, True),  # its grid cell is empty: ingest would sample it
        (2.1, True),  # earlier than the frame sampled in its cell: ingest would take it instead
    ],
)
async def test_a_frame_later_than_the_holdback_is_skipped(
    ctx: StageContext, late: float, recorded: bool
):
    driver = await Driver(ctx).start()
    for t in (0.0, 1.0, 2.4, 4.0, 6.0):  # 6.0 releases everything up to 4.0
        await driver.deliver((t, jpeg(COLOURS[0])))
    await driver.deliver((late, jpeg(COLOURS[1])))
    assert (Lane.SCREEN in driver.timeline.skipped_input) is recorded
    timeline = await driver.stop()

    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    assert [f.t for f in live.frames] == [0.0, 1.0, 2.4, 4.0, 6.0]
    assert (Lane.SCREEN in timeline.skipped_input) is recorded
    # Ingest at stop uses every captured frame; it only differs when the frame was recorded.
    ingest = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert (ingest != live) is recorded


async def test_zero_holdback_feeds_frames_as_they_arrive(tmp_path: Path, events: list):
    ctx = make_ctx(tmp_path, events, live_frame_holdback_s=0)
    driver = await Driver(ctx).start()
    await driver.deliver((0.0, jpeg(COLOURS[0])))
    assert [f.t for f in read_model(ctx.paths.live_frame_index, FrameIndex).frames] == [0.0]
    await driver.stop()


async def test_the_frame_index_is_saved_periodically_not_per_frame(ctx: StageContext):
    def saved() -> list[float]:
        return [f.t for f in read_model(ctx.paths.live_frame_index, FrameIndex).frames]

    driver = await Driver(ctx).start()
    for t in range(13):
        await driver.deliver((float(t), jpeg(COLOURS[0])))
    assert saved() == [0.0]  # written once frames start; not again for INDEX_SAVE_S
    for t in range(13, 33):
        await driver.deliver((float(t), jpeg(COLOURS[0])))
    assert saved() == [float(t) for t in range(31)]  # 30 s of frames since the last save
    await driver.stop()
    assert saved() == [float(t) for t in range(33)]


async def test_no_frames_gives_empty_outputs(ctx: StageContext, events: list):
    timeline = await (await Driver(ctx).start()).stop()
    assert read_model(ctx.paths.live_frame_index, FrameIndex) == FrameIndex(
        fps=1.0, width=0, height=0, frames=[]
    )
    assert read_model(ctx.paths.live_moments, Moments) == Moments(moments=[])
    assert timeline.moments == [] and events == []


async def test_with_no_screen_closed_to_follows_the_transcript(ctx: StageContext, events: list):
    store_capture(ctx.paths, LiveCapture(screen=False))
    tl = LiveTimeline()
    task = asyncio.create_task(ScreenLane(ctx, tl).run())
    await tl.publish_transcript([], transcribed_to=7.5)
    await asyncio.wait_for(tl.wait_until(lambda: tl.closed_to >= 7.5), 5)
    await tl.publish_transcript([], transcribed_to=19.0)
    await asyncio.wait_for(tl.wait_until(lambda: tl.closed_to >= 19.0), 5)
    assert tl.closed_to == 19.0
    await tl.request_end()
    await asyncio.wait_for(task, 5)
    assert tl.moments == [] and events == []
    assert read_model(ctx.paths.live_moments, Moments) == Moments(moments=[])


async def test_a_recording_without_a_capture_file_shares_its_screen(ctx: StageContext):
    """A recording started before capture.json existed, or with an unreadable one."""
    assert read_capture(ctx.paths) == LiveCapture(screen=True)
    ctx.paths.live_capture.parent.mkdir(parents=True, exist_ok=True)
    ctx.paths.live_capture.write_text("{not json")
    assert read_capture(ctx.paths) == LiveCapture(screen=True)
    driver = await Driver(ctx).start()
    await driver.deliver(*((float(t), encode(f)) for t, f in enumerate(three_slides().frames)))
    timeline = await driver.stop()
    assert timeline.moments and timeline.closed_to == timeline.moments[-1].end


async def test_a_fresh_lane_replays_the_captured_frames(ctx: StageContext):
    """After a backend restart the lane starts from the captured files and stale frames go."""
    stale = ctx.paths.frames_dir / "999999999.jpg"
    stale.write_bytes(b"old")
    for t in (0.0, 1.0, 2.0, 3.0):
        store_frame(ctx.paths, t, jpeg(COLOURS[int(t)]))
    driver = await Driver(ctx).start()
    assert [f.t for f in read_model(ctx.paths.live_frame_index, FrameIndex).frames] == [0.0, 1.0]
    await driver.stop()
    assert not stale.exists()
    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    assert live == finalize_frames(ctx.paths, 1.0, 320)


# --- Moments ----------------------------------------------------------------------------------


def deck_ctx(tmp_path: Path, events: list) -> StageContext:
    return make_ctx(tmp_path, events, live_frame_holdback_s=2.0)


async def run_deck(ctx: StageContext, deck: Deck, seed: int = 0) -> tuple[LiveTimeline, list]:
    """Deliver the deck's frames one per second, jittered within the holdback."""
    driver = await Driver(ctx).start()
    data = [(float(i), encode(f)) for i, f in enumerate(deck.frames)]
    rng = random.Random(seed)
    published_live = []
    for i in range(0, len(data), 2):
        pair = data[i : i + 2]
        rng.shuffle(pair)
        await driver.deliver(*pair)
        published_live.append(len(driver.timeline.moments))
    return await driver.stop(), published_live


async def test_moments_match_the_detect_stage_and_close_while_recording(
    tmp_path: Path, events: list
):
    ctx = deck_ctx(tmp_path, events)
    timeline, published_live = await run_deck(ctx, three_slides())

    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    expected = detect_moments(ctx.paths.root, index)
    assert len(expected) == 3
    assert timeline.moments == expected
    assert read_model(ctx.paths.live_moments, Moments) == Moments(moments=expected)
    assert events == [(EventType.MOMENT_FOUND, moment_event(m)) for m in expected]
    # The first two closed while recording; only the last waited for stop.
    assert published_live[-1] == 2
    assert timeline.closed_to == expected[-1].end


async def test_a_brief_flash_does_not_close_a_moment(tmp_path: Path, events: list):
    ctx = deck_ctx(tmp_path, events)
    deck = Deck().hold(SLIDE_A, 10).hold(SLIDE_B, 3).hold(SLIDE_A, 10)
    timeline, published_live = await run_deck(ctx, deck)
    assert max(published_live) == 0
    assert [(m.start, m.end) for m in timeline.moments] == [(0, 23)]
