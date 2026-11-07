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
from iris.models import EventType, FrameIndex, Moments, SourceKind
from iris.pipeline.context import StageContext
from iris.pipeline.stages.detect import detect_moments, moment_event
from iris.sources.browser_live import finalize_frames, store_frame, store_screen_change
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

    async def screen(self, t: float, on: bool) -> None:
        """The screen was turned on or off at ``t``, as the API stores it."""
        store_screen_change(self.ctx.paths, t, on)
        self.idle.clear()
        self.lane.wake()
        await asyncio.wait_for(self.idle.wait(), 10)

    async def transcribed(self, t: float) -> None:
        """The transcript reached ``t``: the clock while no frames come."""
        self.idle.clear()
        await self.timeline.publish_transcript([], transcribed_to=t)
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
    tl = LiveTimeline()  # an audio-only recording: the screen was never turned on
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


async def test_frames_without_a_screen_change_are_still_fed(ctx: StageContext):
    """A recording started before screen changes were stored posts frames with none."""
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


# --- Screen on and off ------------------------------------------------------------------------


def slides(start: int, img: np.ndarray, seconds: int, seed: int) -> list[tuple[float, bytes]]:
    deck = Deck(seed).hold(img, seconds)
    return [(float(start + i), encode(f)) for i, f in enumerate(deck.frames)]


async def test_a_screen_off_closes_the_moment_and_notes_follow_the_transcript_until_on(
    tmp_path: Path, events: list
):
    ctx = deck_ctx(tmp_path, events)
    store_screen_change(ctx.paths, 0.0, on=True)
    driver = await Driver(ctx).start()
    tl = driver.timeline
    await driver.deliver(*slides(0, SLIDE_A, 10, seed=0))
    await driver.screen(10.5, on=False)
    assert tl.moments == []  # the last frames and the off wait out the holdback
    await driver.transcribed(30.0)
    # Closed at the off time while recording, and nothing is open while the screen is off.
    assert [(m.start, m.end) for m in tl.moments] == [(0.0, 10.5)]
    assert tl.closed_to == 30.0
    await driver.screen(40.0, on=True)
    await driver.transcribed(45.0)
    assert tl.closed_to == 40.0  # a moment may start at 40 s: no further
    # A smaller source after the gap: its own size, compared only with itself.
    small = cv2.resize(SLIDE_B, (SLIDE_B.shape[1] // 2, SLIDE_B.shape[0] // 2))
    await driver.deliver(*slides(41, small, 12, seed=1))
    timeline = await driver.stop()

    assert not timeline.skipped_input
    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.5), (41.0, 53.0)]
    # The live outputs are what ingest and the detect stage make at stop, so they are promoted.
    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    live_files = frame_files(ctx.paths)
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert live == index and index.screen_off == [10.5]
    assert live_files == frame_files(ctx.paths)
    assert detect_moments(ctx.paths.root, index) == timeline.moments


async def test_stop_then_continue_starts_a_new_share_at_the_same_time(tmp_path: Path, events: list):
    """Stop releases the share and Continue opens a new one: off and on at the same time."""
    ctx = deck_ctx(tmp_path, events)
    store_screen_change(ctx.paths, 0.0, on=True)
    driver = await Driver(ctx).start()
    await driver.deliver(*slides(0, SLIDE_A, 10, seed=0))
    await driver.screen(10.0, on=False)
    await driver.screen(10.0, on=True)
    await driver.deliver(*slides(10, SLIDE_A, 10, seed=1))  # the same slide again
    timeline = await driver.stop()
    assert not timeline.skipped_input  # an ordinary Stop and Continue: promoted as it is
    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.0), (10.0, 20.0)]
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert detect_moments(ctx.paths.root, index) == timeline.moments


async def test_a_fresh_lane_replays_the_screen_changes_too(tmp_path: Path, events: list):
    """After a backend restart, everything captured (frames and screen changes) is replayed."""
    ctx = deck_ctx(tmp_path, events)
    store_screen_change(ctx.paths, 0.0, on=True)
    for t, data in slides(0, SLIDE_A, 10, seed=0):
        store_frame(ctx.paths, t, data)
    store_screen_change(ctx.paths, 10.5, on=False)
    store_screen_change(ctx.paths, 40.0, on=True)
    small = cv2.resize(SLIDE_B, (SLIDE_B.shape[1] // 2, SLIDE_B.shape[0] // 2))
    for t, data in slides(41, small, 12, seed=1):
        store_frame(ctx.paths, t, data)
    timeline = await (await Driver(ctx).start()).stop()

    assert not timeline.skipped_input
    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.5), (41.0, 53.0)]
    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    live_files = frame_files(ctx.paths)
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert live == index and live_files == frame_files(ctx.paths)
    assert detect_moments(ctx.paths.root, index) == timeline.moments


async def test_an_off_after_later_frames_were_fed_is_skipped(ctx: StageContext):
    store_screen_change(ctx.paths, 0.0, on=True)
    driver = await Driver(ctx).start()
    for t in range(8):
        await driver.deliver((float(t), jpeg(COLOURS[0])))
    await driver.screen(3.5, on=False)  # frames after it were already fed
    assert Lane.SCREEN in driver.timeline.skipped_input
    await driver.stop()


async def test_a_share_s_frames_landing_after_its_off_was_applied_are_skipped(
    tmp_path: Path, events: list
):
    """Share A's frames are still being retried when the next share's screen-on lands and releases
    A's off: fed now they would join the next share, so they are skipped input instead."""
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()  # an audio recording
    await driver.screen(10.0, on=True)
    await driver.screen(20.0, on=False)
    await driver.screen(24.0, on=True)  # 20 + the 2 s holdback: the off is applied
    await driver.deliver(*slides(10, SLIDE_A, 10, seed=0))  # share A's, late
    assert Lane.SCREEN in driver.timeline.skipped_input
    await driver.deliver(*slides(24, SLIDE_A, 12, seed=1))
    timeline = await driver.stop()
    # Stop re-runs detect from everything captured, which splits the shares at the off.
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert [(m.start, m.end) for m in detect_moments(ctx.paths.root, index)] == [
        (10.0, 20.0),
        (24.0, 36.0),
    ]
    assert Lane.SCREEN in timeline.skipped_input


async def test_an_off_at_the_time_of_a_frame_already_fed_is_skipped(ctx: StageContext):
    """Ingest puts a frame at an off time in the next share; live already fed it to this one."""
    store_screen_change(ctx.paths, 0.0, on=True)
    driver = await Driver(ctx).start()
    for t in range(8):  # 0 to 5 are fed
        await driver.deliver((float(t), jpeg(COLOURS[0])))
    await driver.screen(5.0, on=False)
    assert Lane.SCREEN in driver.timeline.skipped_input
    await driver.stop()


async def test_a_frame_from_before_closed_to_is_skipped(ctx: StageContext):
    """The screen was off, so closed_to followed the transcript; a frame then arrives from before
    it (its share's on change came late): moments can no longer start there."""
    driver = await Driver(ctx).start()
    await driver.transcribed(20.0)
    assert driver.timeline.closed_to == 20.0
    await driver.deliver((12.0, jpeg(COLOURS[0])))
    assert Lane.SCREEN in driver.timeline.skipped_input
    timeline = await driver.stop()
    assert timeline.moments == []
