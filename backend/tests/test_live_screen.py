import asyncio
import math
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
from iris.sources.browser_live import finalize_frames, store_frame
from iris.store.layout import SessionPaths, read_model
from tests.test_detect import SLIDE_A, SLIDE_B, SLIDE_C, Deck, three_slides
from tests.test_live import COLOURS, jpeg


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
    def __init__(self, ctx: StageContext):
        self.ctx = ctx
        self.timeline = LiveTimeline()
        self.lane = ScreenLane(ctx, self.timeline)
        self.idle = asyncio.Event()
        wait = self.lane.wait_for_input

        async def waiting() -> None:
            self.idle.set()
            await wait()

        self.lane.wait_for_input = waiting  # type: ignore[method-assign]
        self.task: asyncio.Task[None] | None = None

    async def start(self) -> "Driver":
        self.task = asyncio.create_task(self.lane.run())
        await asyncio.wait_for(self.idle.wait(), 10)
        return self

    async def deliver(self, *frames: tuple[float, bytes], share: int = 1) -> None:
        await self.deliver_shared(*((t, share, data) for t, data in frames))

    async def deliver_shared(self, *frames: tuple[float, int, bytes]) -> None:
        for t, share, data in frames:
            store_frame(self.ctx.paths, t, share, data)
        self.idle.clear()
        self.lane.wake()
        await asyncio.wait_for(self.idle.wait(), 10)

    async def transcribed(self, t: float) -> None:
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


async def test_shuffled_frames_within_the_holdback_match_ingest(ctx: StageContext):
    frames = {
        0.0: b"\xff\xd8\xff truncated",
        1.02: jpeg(COLOURS[1], (640, 480)),
        1.6: jpeg(COLOURS[2], (640, 480)),
        2.05: jpeg(COLOURS[3], (640, 480)),
        3.5: jpeg(COLOURS[4], (320, 240)),
        4.3: jpeg(COLOURS[5], (640, 480)),
        5.0: jpeg(COLOURS[0], (640, 240)),
        6.1: jpeg(COLOURS[1], (640, 480)),
        7.0: jpeg(COLOURS[2], (640, 480)),
    }
    deliveries = [[1.02, 0.0], [2.05, 1.6], [4.3], [3.5], [6.1, 5.0], [7.0]]
    driver = await Driver(ctx).start()
    latest = 0.0
    for times in deliveries:
        await driver.deliver(*((t, frames[t]) for t in times))
        latest = max(latest, *times)
        if ctx.paths.live_frame_index.exists():
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
        (1.5, False),
        (3.0, True),
        (2.1, True),
    ],
)
async def test_a_frame_later_than_the_holdback_is_skipped(
    ctx: StageContext, late: float, recorded: bool
):
    driver = await Driver(ctx).start()
    for t in (0.0, 1.0, 2.4, 4.0, 6.0):
        await driver.deliver((t, jpeg(COLOURS[0])))
    await driver.deliver((late, jpeg(COLOURS[1])))
    assert (Lane.SCREEN in driver.timeline.skipped_input) is recorded
    timeline = await driver.stop()

    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    assert [f.t for f in live.frames] == [0.0, 1.0, 2.4, 4.0, 6.0]
    assert (Lane.SCREEN in timeline.skipped_input) is recorded
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
    assert saved() == [0.0]
    for t in range(13, 33):
        await driver.deliver((float(t), jpeg(COLOURS[0])))
    assert saved() == [float(t) for t in range(31)]
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


async def test_a_fresh_lane_replays_the_captured_frames(ctx: StageContext):
    stale = ctx.paths.frames_dir / "999999999.jpg"
    stale.write_bytes(b"old")
    for t in (0.0, 1.0, 2.0, 3.0):
        store_frame(ctx.paths, t, 1, jpeg(COLOURS[int(t)]))
    driver = await Driver(ctx).start()
    assert [f.t for f in read_model(ctx.paths.live_frame_index, FrameIndex).frames] == [0.0, 1.0]
    await driver.stop()
    assert not stale.exists()
    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    assert live == finalize_frames(ctx.paths, 1.0, 320)


def deck_ctx(tmp_path: Path, events: list) -> StageContext:
    return make_ctx(tmp_path, events, live_frame_holdback_s=2.0)


async def run_deck(ctx: StageContext, deck: Deck, seed: int = 0) -> tuple[LiveTimeline, list]:
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
    assert published_live[-1] == 2
    assert timeline.closed_to == expected[-1].end


async def test_a_brief_flash_does_not_close_a_moment(tmp_path: Path, events: list):
    ctx = deck_ctx(tmp_path, events)
    deck = Deck().hold(SLIDE_A, 10).hold(SLIDE_B, 3).hold(SLIDE_A, 10)
    timeline, published_live = await run_deck(ctx, deck)
    assert max(published_live) == 0
    assert [(m.start, m.end) for m in timeline.moments] == [(0, 23)]


def slides(start: float, img: np.ndarray, seconds: int, seed: int) -> list[tuple[float, bytes]]:
    deck = Deck(seed).hold(img, seconds)
    return [(float(start + i), encode(f)) for i, f in enumerate(deck.frames)]


def assert_matches_ingest(ctx: StageContext, timeline: LiveTimeline) -> FrameIndex:
    assert not timeline.skipped_input
    live = read_model(ctx.paths.live_frame_index, FrameIndex)
    live_files = frame_files(ctx.paths)
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert live == index
    assert live_files == frame_files(ctx.paths)
    assert detect_moments(ctx.paths.root, index) == timeline.moments
    return index


async def test_the_next_share_closes_the_moment_where_the_share_before_ended(
    tmp_path: Path, events: list
):
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()
    tl = driver.timeline
    await driver.deliver(*slides(0, SLIDE_A, 10, seed=0))
    await driver.transcribed(30.0)
    assert tl.moments == [] and tl.closed_to == 0.0
    small = cv2.resize(SLIDE_B, (SLIDE_B.shape[1] // 2, SLIDE_B.shape[0] // 2))
    await driver.deliver(*slides(41, small, 12, seed=1), share=2)
    assert [(m.start, m.end) for m in tl.moments] == [(0.0, 10.0)]
    timeline = await driver.stop()

    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.0), (41.0, 53.0)]
    assert assert_matches_ingest(ctx, timeline).share_starts == [41.0]


async def test_stop_then_continue_starts_a_new_share_at_the_same_time(tmp_path: Path, events: list):
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()
    await driver.deliver(*slides(0, SLIDE_A, 10, seed=0))
    await driver.deliver(*slides(10, SLIDE_A, 10, seed=1), share=2)
    timeline = await driver.stop()
    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.0), (10.0, 20.0)]
    assert_matches_ingest(ctx, timeline)


async def test_closed_to_follows_the_transcript_until_the_first_share(tmp_path: Path, events: list):
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()
    tl = driver.timeline
    await driver.transcribed(20.0)
    assert tl.closed_to == 20.0
    await driver.deliver(*slides(25, SLIDE_A, 10, seed=0))
    await driver.transcribed(40.0)
    assert tl.closed_to == 20.0
    timeline = await driver.stop()
    assert [(m.start, m.end) for m in timeline.moments] == [(25.0, 35.0)]
    assert_matches_ingest(ctx, timeline)


async def test_a_share_continued_within_a_grid_step_ends_where_the_next_starts(
    tmp_path: Path, events: list
):
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()
    await driver.deliver(*((t + 0.3, data) for t, data in slides(0, SLIDE_A, 10, seed=0)))
    await driver.deliver(*((t + 0.1, data) for t, data in slides(10, SLIDE_B, 10, seed=1)), share=2)
    timeline = await driver.stop()
    assert [(m.start, m.end) for m in timeline.moments] == [(0.3, 10.1), (10.1, 20.1)]
    assert_matches_ingest(ctx, timeline)


async def test_an_earlier_share_s_frame_at_the_same_time_is_skipped_once_the_next_s_is_fed(
    ctx: StageContext,
):
    driver = await Driver(ctx).start()
    await driver.deliver((9.0, jpeg(COLOURS[0])))
    await driver.deliver(*((t, jpeg(COLOURS[1])) for t in (10.0, 12.0)), share=2)
    await driver.deliver((10.0, jpeg(COLOURS[2])))
    assert Lane.SCREEN in driver.timeline.skipped_input
    await driver.stop()


async def test_a_fresh_lane_replays_every_share(tmp_path: Path, events: list):
    ctx = deck_ctx(tmp_path, events)
    for t, data in slides(0, SLIDE_A, 10, seed=0):
        store_frame(ctx.paths, t, 1, data)
    small = cv2.resize(SLIDE_B, (SLIDE_B.shape[1] // 2, SLIDE_B.shape[0] // 2))
    for t, data in slides(41, small, 12, seed=1):
        store_frame(ctx.paths, t, 2, data)
    timeline = await (await Driver(ctx).start()).stop()
    assert [(m.start, m.end) for m in timeline.moments] == [(0.0, 10.0), (41.0, 53.0)]
    assert_matches_ingest(ctx, timeline)


async def test_a_share_s_frames_landing_after_the_next_share_s_are_skipped(
    tmp_path: Path, events: list
):
    ctx = deck_ctx(tmp_path, events)
    driver = await Driver(ctx).start()
    await driver.deliver(*slides(24, SLIDE_A, 12, seed=1), share=2)
    await driver.deliver(*slides(10, SLIDE_B, 10, seed=0), share=1)
    assert Lane.SCREEN in driver.timeline.skipped_input
    timeline = await driver.stop()
    assert Lane.SCREEN in timeline.skipped_input
    index = finalize_frames(ctx.paths, ctx.settings.frame_fps, ctx.settings.frame_max_width)
    assert [(m.start, m.end) for m in detect_moments(ctx.paths.root, index)] == [
        (10.0, 20.0),
        (24.0, 36.0),
    ]


async def test_a_frame_from_before_closed_to_is_skipped(ctx: StageContext):
    driver = await Driver(ctx).start()
    await driver.transcribed(20.0)
    assert driver.timeline.closed_to == 20.0
    await driver.deliver((12.0, jpeg(COLOURS[0])))
    assert Lane.SCREEN in driver.timeline.skipped_input
    timeline = await driver.stop()
    assert timeline.moments == []


def random_recording(rng: random.Random) -> list[tuple[float, int, bytes]]:
    images = [SLIDE_A, SLIDE_B, SLIDE_C]
    frames: list[tuple[float, int, bytes]] = []
    t = 0.0
    for share in range(1, rng.randint(2, 4) + 1):
        exact: float | None = None
        if share == 2:
            last = frames[-1][0]
            exact = math.floor(last) + 1 + (last % 1) / 2
            t = math.floor(last) + 1
        elif share > 2:
            t += rng.choice([0.0, 0.4, 7.0, 30.0])
        for _ in range(rng.randint(1, 3)):
            seconds = rng.randint(1, 12)
            deck = Deck(rng.randrange(1000)).hold(rng.choice(images), seconds)
            for frame in deck.frames:
                at = exact if exact is not None else t + rng.uniform(0.1, 0.3)
                frames.append((round(at, 3), share, encode(frame)))
                exact = None
                t += 1.0
            if rng.random() < 0.4:
                t += rng.choice([2.0, 5.0, 20.0])
    return frames


@pytest.mark.parametrize("seed", range(6))
async def test_random_shares_and_gaps_match_ingest(tmp_path: Path, events: list, seed: int):
    ctx = deck_ctx(tmp_path, events)
    rng = random.Random(seed)
    frames = random_recording(rng)
    second = next(i for i, f in enumerate(frames) if f[1] == 2)
    assert frames[second][0] - frames[second - 1][0] < 1.0
    arrival = list(frames)
    for i in range(0, len(arrival) - 1, 2):
        if arrival[i + 1][0] - arrival[i][0] < 1.9 and rng.random() < 0.7:
            arrival[i], arrival[i + 1] = arrival[i + 1], arrival[i]
    assert arrival != frames
    driver = await Driver(ctx).start()
    for frame in arrival:
        await driver.deliver_shared(frame)
    timeline = await driver.stop()
    assert timeline.moments
    assert_matches_ingest(ctx, timeline)
