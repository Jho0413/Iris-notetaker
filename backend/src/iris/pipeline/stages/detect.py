import asyncio
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from iris.media.images import grey, shrink_to_width, size_within_width
from iris.models import Box, Build, EventType, FrameIndex, FrameRef, Moment, Moments
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model

SHARPNESS_MAX_WIDTH = 640


@dataclass(frozen=True)
class DetectParams:
    work_width: int = 320
    blur_ksize: int = 5
    pixel_threshold: int = 25

    same_frac: float = 0.005
    dup_frac: float = 0.01

    settle_s: float = 2.0
    min_settle_frames: int = 2
    min_moment_s: float = 4.0

    merge_builds: bool = True
    additive_keep: float = 0.85

    activity_decay: float = 0.8
    activity_threshold: float = 0.6
    max_mask_frac: float = 0.35

    region_dilate: int = 9
    region_min_blob_frac: float = 0.0005
    region_max_frac: float = 0.6
    region_pad: int = 8

    scroll_max_frac: float = 0.6
    scroll_min_edges: int = 40


@dataclass
class _State:
    start: float
    small: np.ndarray
    edges: np.ndarray
    frame: FrameRef
    sharpness: float
    last_t: float
    mask_frac: float
    region: Box | None = None
    builds: list[Build] = field(default_factory=list)

    def consider(self, t: float, ref: FrameRef, sharpness: float, can_represent: bool) -> None:
        self.last_t = t
        if can_represent and sharpness > self.sharpness:
            self.frame, self.sharpness = ref, sharpness

    def replace_content(self, other: "_State", build: Build | None = None) -> None:
        if build is not None:
            self.builds.append(build)
        self.small, self.edges = other.small, other.edges
        self.frame, self.sharpness = other.frame, other.sharpness
        self.last_t, self.mask_frac = other.last_t, other.mask_frac


@dataclass
class _Pending:
    onset: float
    small: np.ndarray
    frames: list[tuple[float, FrameRef, float]] = field(default_factory=list)


_OPEN_KERNEL = np.ones((3, 3), np.uint8)
_MASK_KERNEL = np.ones((5, 5), np.uint8)
_EMPTY_BACKGROUND_MAX_EDGES = 20


class MomentDetector:
    def __init__(self, params: DetectParams | None = None):
        self.p = params or DetectParams()
        self._count = 0
        self._new_share()

    def _new_share(self) -> None:
        self._current: _State | None = None
        self._next: _State | None = None
        self._pending: _Pending | None = None
        self._fallback: tuple[FrameRef, float] | None = None
        self._first_t: float | None = None
        self._share_start_count = self._count
        self._work_size: tuple[int, int] | None = None
        self._frame_size: tuple[int, int] = (0, 0)
        self._prev_small: np.ndarray | None = None
        self._activity: np.ndarray | None = None
        self._mask: np.ndarray | None = None

    def feed(self, t: float, frame_ref: FrameRef, image: np.ndarray) -> Moment | None:
        small = self._prepare(image)
        self._update_mask(small)
        sharpness = _sharpness(image)
        if self._first_t is None:
            self._first_t = t

        if (
            self._current is None
            and self._count == self._share_start_count
            and (self._fallback is None or sharpness > self._fallback[1])
        ):
            self._fallback = (frame_ref, sharpness)

        active = self._next or self._current
        if active is not None and self._same(small, active.small):
            active.consider(t, frame_ref, sharpness, self._can_represent(active))
            self._pending = None
        elif (
            self._next is not None
            and self._current is not None
            and self._same(small, self._current.small)
        ):
            self._next = self._pending = None
            self._current.consider(t, frame_ref, sharpness, self._can_represent(self._current))
        else:
            self._add_pending(t, frame_ref, small, sharpness)
            if self._is_settled():
                self._accept()
        return self._promote()

    def finish(self, end_t: float) -> Moment | None:
        current, self._current = self._current, None
        fallback, self._fallback = self._fallback, None
        pending = self._pending
        self._next = self._pending = None
        if current is None:
            if (
                self._count == self._share_start_count
                and fallback is not None
                and pending is not None
            ):
                ref, _ = fallback
                assert self._first_t is not None
                self._count += 1
                return Moment(
                    id=f"m{self._count:03d}",
                    start=round(self._first_t, 3),
                    end=round(max(end_t, pending.frames[-1][0]), 3),
                    frame=ref,
                )
            return None
        return self._emit(current, max(end_t, current.last_t))

    def end_share(self, end_t: float) -> Moment | None:
        moment = self.finish(end_t)
        self._new_share()
        return moment

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        gray = grey(image)
        h, w = gray.shape
        self._frame_size = (w, h)
        if self._work_size is None:
            self._work_size = size_within_width(w, h, self.p.work_width)
        if (w, h) != self._work_size:
            gray = cv2.resize(gray, self._work_size, interpolation=cv2.INTER_AREA)
        k = self.p.blur_ksize
        return cv2.GaussianBlur(gray, (k, k), 0) if k > 1 else gray

    def _update_mask(self, small: np.ndarray) -> None:
        if self._prev_small is not None:
            moving = (cv2.absdiff(small, self._prev_small) > self.p.pixel_threshold).astype(
                np.float32
            )
            if self._activity is None:
                self._activity = np.zeros_like(moving)
            d = self.p.activity_decay
            self._activity = d * self._activity + (1 - d) * moving
            hot = (self._activity > self.p.activity_threshold).astype(np.uint8)
            frac = float(hot.mean())
            if 0 < frac <= self.p.max_mask_frac:
                self._mask = cv2.dilate(hot, _MASK_KERNEL).astype(bool)
            else:
                self._mask = None
        self._prev_small = small

    def _mask_frac(self) -> float:
        return float(self._mask.mean()) if self._mask is not None else 0.0

    def _can_represent(self, state: _State) -> bool:
        mask_grew_since_settling = self._mask_frac() > state.mask_frac + 0.02
        return not mask_grew_since_settling

    def _changed(self, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        diff = (cv2.absdiff(a, b) > self.p.pixel_threshold).astype(np.uint8)
        if self._mask is not None:
            diff[self._mask] = 0
        return cv2.morphologyEx(diff, cv2.MORPH_OPEN, _OPEN_KERNEL)

    def _diff_frac(self, a: np.ndarray, b: np.ndarray) -> float:
        changed = self._changed(a, b)
        return cv2.countNonZero(changed) / changed.size

    def _same(self, a: np.ndarray, b: np.ndarray) -> bool:
        return self._diff_frac(a, b) < self.p.same_frac

    def _add_pending(self, t: float, ref: FrameRef, small: np.ndarray, sharpness: float) -> None:
        pending = self._pending
        if pending is None or not self._same(small, pending.small):
            onset = pending.onset if pending is not None else t
            pending = self._pending = _Pending(onset=onset, small=small)
        pending.frames.append((t, ref, sharpness))

    def _is_settled(self) -> bool:
        frames = self._pending.frames if self._pending else []
        return (
            len(frames) >= self.p.min_settle_frames
            and frames[-1][0] - frames[0][0] >= self.p.settle_s - 1e-6
        )

    def _accept(self) -> None:
        pending, self._pending = self._pending, None
        assert pending is not None
        _, ref, sharpness = max(pending.frames, key=lambda f: f[2])
        settled_t = pending.frames[0][0]
        state = _State(
            start=pending.onset,
            small=pending.small,
            edges=_edges(pending.small),
            frame=ref,
            sharpness=sharpness,
            last_t=pending.frames[-1][0],
            mask_frac=self._mask_frac(),
        )
        if self._current is None:
            state.start = self._first_t if self._first_t is not None else state.start
            state.builds = [Build(t=round(settled_t, 3), frame=ref)]
            self._current = state
            return
        for old in (self._next, self._current):
            if old is None:
                continue
            absorbed = self._absorbs(old, state, settled_t)
            if absorbed is not False:
                if old is self._current:
                    self._next = None
                old.replace_content(state, absorbed)
                return
        if self._next is not None:
            state.start = self._next.start
        state.region = self._region(self._changed(self._current.small, state.small))
        state.builds = [Build(t=round(settled_t, 3), frame=ref, region=state.region)]
        self._next = state

    def _absorbs(self, old: _State, new: _State, settled_t: float) -> Build | None | bool:
        changed = self._changed(old.small, new.small)
        small_change = cv2.countNonZero(changed) / changed.size < self.p.dup_frac
        if (self.p.merge_builds or small_change) and self._is_additive(old, new, changed):
            return Build(t=round(settled_t, 3), frame=new.frame, region=self._region(changed))
        if small_change:
            return None
        if self.p.merge_builds and (dy := self._scroll(old, new)) is not None:
            return self._scroll_build(old, new, dy, settled_t)
        return False

    def _scroll(self, old: _State, new: _State) -> int | None:
        h = old.small.shape[0]
        static = cv2.absdiff(old.small, new.small) <= self.p.pixel_threshold
        moving = old.edges & ~static
        if self._mask is not None:
            moving &= ~self._mask
        ys, xs = np.nonzero(moving)
        if len(ys) < self.p.scroll_min_edges:
            return None
        target = cv2.dilate(new.edges.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        best_score, best_dy = 0.0, 0
        limit = int(h * self.p.scroll_max_frac)
        for dy in range(-limit, limit + 1):
            if abs(dy) < 2:
                continue
            y2 = ys + dy
            inside = (y2 >= 0) & (y2 < h)
            score = float(target[y2[inside], xs[inside]].sum()) / len(ys)
            if score > best_score:
                best_score, best_dy = score, dy
        return best_dy if best_score >= self.p.additive_keep else None

    def _scroll_build(self, old: _State, new: _State, dy: int, settled_t: float) -> Build:
        h = old.small.shape[0]
        shifted = np.zeros_like(old.edges)
        if dy > 0:
            shifted[dy:] = old.edges[: h - dy]
        else:
            shifted[: h + dy] = old.edges[-dy:]
        explained = cv2.dilate(shifted.astype(np.uint8), _MASK_KERNEL).astype(bool)
        static = cv2.absdiff(old.small, new.small) <= self.p.pixel_threshold
        fresh = (new.edges & ~explained & ~static).astype(np.uint8)
        fh = self._frame_size[1]
        shift = round(dy * fh / h)
        for build in old.builds:
            if build.region is None:
                continue
            y0 = max(0, build.region.y + shift)
            y1 = min(fh, build.region.y + build.region.h + shift)
            build.region = (
                Box(x=build.region.x, y=y0, w=build.region.w, h=y1 - y0) if y1 > y0 else None
            )
        region = self._region(cv2.dilate(fresh, _OPEN_KERNEL)) if fresh.any() else None
        return Build(t=round(settled_t, 3), frame=new.frame, region=region)

    def _is_additive(self, old: _State, new: _State, changed: np.ndarray) -> bool:
        area = cv2.dilate(changed, _MASK_KERNEL).astype(bool)
        old_edges = old.edges & area
        n = int(old_edges.sum())
        if n < _EMPTY_BACKGROUND_MAX_EDGES:
            return True
        new_edges = cv2.dilate(new.edges.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        return (old_edges & new_edges).sum() / n >= self.p.additive_keep

    def _promote(self) -> Moment | None:
        nxt = self._next
        if nxt is None or nxt.last_t - nxt.start < self.p.min_moment_s:
            return None
        current, self._current, self._next = self._current, nxt, None
        assert current is not None
        if nxt.start - current.start < self.p.min_moment_s:
            nxt.start, nxt.region = current.start, None
            return None
        return self._emit(current, nxt.start)

    def _emit(self, state: _State, end: float) -> Moment:
        self._count += 1
        return Moment(
            id=f"m{self._count:03d}",
            start=round(state.start, 3),
            end=round(end, 3),
            frame=state.frame,
            changed_region=state.region,
            builds=state.builds,
        )

    def _region(self, changed: np.ndarray) -> Box | None:
        k = self.p.region_dilate
        blobs = cv2.dilate(changed, np.ones((k, k), np.uint8))
        contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = self.p.region_min_blob_frac * blobs.size
        rects = [r for r in map(cv2.boundingRect, contours) if r[2] * r[3] >= min_area]
        if not rects:
            return None
        dilation_growth = k // 2
        x0 = min(r[0] for r in rects) + dilation_growth
        y0 = min(r[1] for r in rects) + dilation_growth
        x1 = max(r[0] + r[2] for r in rects) - dilation_growth
        y1 = max(r[1] + r[3] for r in rects) - dilation_growth
        if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) > self.p.region_max_frac * blobs.size:
            return None
        fw, fh = self._frame_size
        sx, sy = fw / blobs.shape[1], fh / blobs.shape[0]
        pad = self.p.region_pad
        bx0 = max(0, int(x0 * sx) - pad)
        by0 = max(0, int(y0 * sy) - pad)
        bx1 = min(fw, int(np.ceil(x1 * sx)) + pad)
        by1 = min(fh, int(np.ceil(y1 * sy)) + pad)
        return Box(x=bx0, y=by0, w=bx1 - bx0, h=by1 - by0)


def _edges(small: np.ndarray) -> np.ndarray:
    return cv2.Canny(small, 50, 150) > 0


def _sharpness(image: np.ndarray) -> float:
    gray = shrink_to_width(grey(image), SHARPNESS_MAX_WIDTH)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def share_end(last_t: float, fps: float, next_start: float = math.inf) -> float:
    step = 1.0 / fps if fps > 0 else 0.0
    return min(last_t + step, next_start)


def detect_moments(
    root: Path,
    index: FrameIndex,
    params: DetectParams | None = None,
    on_moment: Callable[[Moment], None] | None = None,
) -> list[Moment]:
    detector = MomentDetector(params)
    moments: list[Moment] = []
    starts = iter(index.share_starts)
    next_start = next(starts, None)

    def keep(m: Moment | None) -> None:
        if m is not None:
            moments.append(m)
            if on_moment is not None:
                on_moment(m)

    for i, ref in enumerate(index.frames):
        if next_start is not None and ref.t >= next_start:
            if i > 0:
                keep(detector.end_share(share_end(index.frames[i - 1].t, index.fps, ref.t)))
            next_start = next(starts, None)
        image = cv2.imread(str(root / ref.path), cv2.IMREAD_COLOR)
        if image is None:
            continue
        keep(detector.feed(ref.t, ref, image))
    if index.frames:
        keep(detector.finish(share_end(index.frames[-1].t, index.fps)))
    return moments


def moment_event(m: Moment) -> dict:
    return {"id": m.id, "start": m.start, "end": m.end, "frame": m.frame.path}


async def run(ctx: StageContext, params: DetectParams | None = None) -> None:
    index = read_model(ctx.paths.frame_index, FrameIndex)
    loop = asyncio.get_running_loop()

    def on_moment(m: Moment) -> None:
        loop.call_soon_threadsafe(ctx.emit, EventType.MOMENT_FOUND, moment_event(m))

    moments = await asyncio.to_thread(detect_moments, ctx.paths.root, index, params, on_moment)
    write_model(ctx.paths.moments, Moments(moments=moments))
