"""Moment detection on synthetic slide decks built in memory."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from iris.config import Settings
from iris.models import EventType, FrameIndex, FrameRef, Moment, Moments, SourceKind
from iris.pipeline.context import StageContext
from iris.pipeline.stages import detect
from iris.pipeline.stages.detect import DetectParams, MomentDetector
from iris.store.layout import SessionPaths, read_model, write_model

W, H = 640, 360
BULLET_Y = 280  # baseline of the third bullet, added by the slide build


def slide(title: str, bullets: int = 2, shape: str = "rect") -> np.ndarray:
    img = np.full((H, W, 3), 255, np.uint8)
    cv2.putText(img, title, (40, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (20, 20, 20), 3)
    for i in range(bullets):
        y = 140 + i * 55 if i < 2 else BULLET_Y
        cv2.circle(img, (75, y - 10), 7, (40, 40, 40), -1)
        cv2.putText(
            img, f"{title} point {i + 1}", (95, y), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2
        )
    if shape == "rect":
        cv2.rectangle(img, (470, 130), (600, 300), (200, 80, 30), -1)
    elif shape == "circle":
        cv2.circle(img, (530, 220), 70, (30, 160, 30), -1)
    else:
        pts = np.array([[530, 130], [610, 300], [450, 300]], np.int32)
        cv2.fillPoly(img, [pts], (30, 30, 200))
    return img


SLIDE_A = slide("Agenda", shape="rect")
SLIDE_B = slide("Results", shape="circle")
SLIDE_C = slide("Next steps", shape="tri")


class Deck:
    """Builds a frame sequence at 1 fps with noise; remembers which times are transitions."""

    def __init__(self, seed: int = 0):
        self.frames: list[np.ndarray] = []
        self.transitions: set[float] = set()
        self.rng = np.random.default_rng(seed)

    @property
    def t(self) -> float:
        return float(len(self.frames))

    def _add(self, img: np.ndarray) -> None:
        noise = self.rng.normal(0, 4, img.shape)
        self.frames.append(np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8))

    def hold(self, img: np.ndarray, seconds: int, overlay=None) -> "Deck":
        for i in range(seconds):
            frame = img.copy()
            if overlay is not None:
                overlay(frame, i, self.rng)
            self._add(frame)
        return self

    def fade(self, a: np.ndarray, b: np.ndarray) -> "Deck":
        self.transitions.add(self.t)
        self._add(cv2.addWeighted(a, 0.5, b, 0.5, 0))
        return self

    def refs(self) -> list[FrameRef]:
        return [
            FrameRef(t=float(i), path=f"frames/{i * 1000:09d}.jpg") for i in range(len(self.frames))
        ]

    def detect(self, params: DetectParams | None = None) -> list[Moment]:
        det = MomentDetector(params)
        out = [det.feed(r.t, r, f) for r, f in zip(self.refs(), self.frames, strict=True)]
        out.append(det.finish(self.t))
        return [m for m in out if m is not None]


def cursor(frame: np.ndarray, i: int, _rng) -> None:
    """A small arrow-like cursor wandering over the slide, plus a blinking caret."""
    x, y = 100 + 37 * i % 400, 120 + 23 * i % 200
    pts = np.array([[x, y], [x, y + 18], [x + 5, y + 13], [x + 12, y + 13]], np.int32)
    cv2.fillPoly(frame, [pts], (0, 0, 0))
    if i % 2:
        cv2.line(frame, (300, 300), (300, 318), (0, 0, 0), 2)


def thumbnails(frame: np.ndarray, _i: int, rng) -> None:
    """Video-call participant thumbnails flickering in the bottom-right corner."""
    frame[290:350, 500:630] = rng.integers(0, 255, (60, 130, 3), dtype=np.uint8)


def three_slides(overlay_b=None, overlay_all=None) -> Deck:
    d = Deck()
    d.hold(SLIDE_A, 10, overlay_all).fade(SLIDE_A, SLIDE_B)
    d.hold(SLIDE_B, 10, overlay_b or overlay_all).fade(SLIDE_B, SLIDE_C)
    return d.hold(SLIDE_C, 10, overlay_all)


def assert_not_transition(moments: list[Moment], deck: Deck) -> None:
    for m in moments:
        assert m.frame.t not in deck.transitions
        assert m.start <= m.frame.t < m.end


def test_three_slides_with_crossfade():
    deck = three_slides()
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 21), (21, 32)]
    assert [m.id for m in moments] == ["m001", "m002", "m003"]
    assert_not_transition(moments, deck)
    assert moments[0].changed_region is None
    # A whole new slide is a full-screen change: no region to crop.
    assert moments[1].changed_region is None and moments[2].changed_region is None


def test_cursor_and_caret_do_not_create_moments():
    deck = three_slides(overlay_b=cursor)
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 21), (21, 32)]
    assert_not_transition(moments, deck)


def test_flickering_thumbnails_are_ignored():
    deck = three_slides(overlay_all=thumbnails)
    moments = deck.detect()
    assert len(moments) == 3
    # Boundaries stay at the slide changes (the first moment's start is the session start).
    assert [m.start for m in moments] == [0, 10, 21]
    assert_not_transition(moments, deck)


def test_slide_build_updates_moment_with_most_complete_state():
    built = slide("Agenda", bullets=3, shape="rect")
    deck = Deck().hold(SLIDE_A, 10).fade(SLIDE_A, built).hold(built, 10)
    deck.fade(built, SLIDE_B).hold(SLIDE_B, 10)
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 21), (21, 32)]
    # Representative shows the bullet (taken from the built stretch, after the fade).
    assert 11 <= moments[0].frame.t < 21
    assert_not_transition(moments, deck)


def _covers(box, target, slack: int = 40) -> bool:
    x, y, w, h = target
    return (
        box.x <= x + 5
        and box.y <= y + 5
        and box.x + box.w >= x + w - 5
        and box.y + box.h >= y + h - 5
        and box.w <= w + 2 * slack
        and box.h <= h + 2 * slack
    )


def bullet_box(title: str) -> tuple[int, int, int, int]:
    """x, y, w, h of the third bullet (dot + text) drawn by ``slide``."""
    (tw, th), base = cv2.getTextSize(f"{title} point 3", cv2.FONT_HERSHEY_SIMPLEX, 0.9, 2)
    top = min(BULLET_Y - th, BULLET_Y - 17)
    return 68, top, 95 + tw - 68, BULLET_Y + base - top


def test_slide_build_as_separate_moment_reports_bullet_region():
    built = slide("Agenda", bullets=3, shape="rect")
    deck = Deck().hold(SLIDE_A, 10).fade(SLIDE_A, built).hold(built, 10)
    moments = deck.detect(DetectParams(merge_builds=False))
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 21)]
    region = moments[1].changed_region
    assert region is not None
    assert _covers(region, bullet_box("Agenda")), region


def test_overlay_covering_content_is_a_new_moment_with_region():
    popup = SLIDE_A.copy()
    cv2.rectangle(popup, (80, 110), (360, 220), (230, 230, 230), -1)
    cv2.rectangle(popup, (80, 110), (360, 220), (90, 90, 90), 2)
    deck = Deck().hold(SLIDE_A, 10).hold(popup, 10)
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 20)]
    region = moments[1].changed_region
    assert region is not None
    assert _covers(region, (80, 110, 280, 110)), region


def test_brief_flash_to_another_slide_is_absorbed():
    deck = Deck().hold(SLIDE_A, 10).hold(SLIDE_B, 3).hold(SLIDE_A, 10)
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 23)]
    assert not 10 <= moments[0].frame.t < 13


def test_returning_to_a_previous_slide_is_a_new_moment():
    deck = Deck().hold(SLIDE_A, 10).hold(SLIDE_B, 10).hold(SLIDE_A, 10)
    moments = deck.detect()
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 20), (20, 30)]


def test_moments_close_incrementally():
    deck = three_slides()
    det = MomentDetector()
    closed_at = {}
    for ref, frame in zip(deck.refs(), deck.frames, strict=True):
        m = det.feed(ref.t, ref, frame)
        if m is not None:
            closed_at[m.id] = ref.t
    last = det.finish(deck.t)
    # Each moment is returned shortly after the next slide has settled for min_moment_s.
    assert closed_at == {"m001": 14.0, "m002": 25.0}
    assert last is not None and last.id == "m003"


def test_empty_detector_finishes_with_nothing():
    assert MomentDetector().finish(10.0) is None


@pytest.fixture
def events() -> list[tuple[EventType, dict]]:
    return []


@pytest.fixture
def ctx(tmp_path: Path, events: list) -> StageContext:
    return StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path / "s1").ensure(),
        settings=Settings(data_dir=tmp_path),
        emit=lambda type, data: events.append((type, data)),
    )


async def test_run_reads_frames_and_writes_moments(ctx: StageContext, events: list):
    deck = three_slides(overlay_b=cursor)
    refs = deck.refs()
    for ref, frame in zip(refs, deck.frames, strict=True):
        cv2.imwrite(str(ctx.paths.root / ref.path), frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    write_model(ctx.paths.frame_index, FrameIndex(fps=1.0, width=W, height=H, frames=refs))

    await detect.run(ctx)

    moments = read_model(ctx.paths.moments, Moments).moments
    assert [(m.start, m.end) for m in moments] == [(0, 10), (10, 21), (21, 32)]
    assert all(m.frame in refs for m in moments)
    assert_not_transition(moments, deck)
    assert [e[0] for e in events] == [EventType.MOMENT_FOUND] * 3
    assert events[1][1] == {"id": "m002", "start": 10, "end": 21, "frame": moments[1].frame.path}


async def test_run_with_no_frames_writes_empty_moments(ctx: StageContext, events: list):
    write_model(ctx.paths.frame_index, FrameIndex(fps=1.0, width=0, height=0, frames=[]))
    await detect.run(ctx)
    assert read_model(ctx.paths.moments, Moments).moments == []
    assert events == []
