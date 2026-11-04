"""Detect moments: sampled frames -> moments where the screen changed and then settled.

Reads:  frames.json (FrameIndex), frames/*.jpg
Writes: moments.json (Moments)
Emits:  MOMENT_FOUND per moment.

Local work only, no model calls (docs/architecture/overview.md, principle 3). Pure OpenCV +
numpy: the work is a "has it settled, and is it different from what we had" state machine, which
PySceneDetect's cut detectors do not model (they flag cuts, not settled states, builds or changed
regions).

How it works (``MomentDetector``, incremental so live sessions can feed it frame by frame):

  1. Each frame is converted to a small blurred grayscale image (``work_width`` wide). Two images
     "differ" by the fraction of pixels whose absolute difference exceeds ``pixel_threshold``,
     after a 3x3 opening that removes isolated specks. Blur + pixel threshold absorb sensor and
     compression noise; the fraction thresholds absorb small things such as a mouse cursor, a
     blinking caret or a ticking clock.
  2. Regions that keep changing frame after frame (video-call thumbnails, an embedded video, a
     cursor being waved around) build up "activity" and are masked out of all comparisons. The mask
     is dropped when it would cover most of the frame (that is a scroll or a cut, not a widget).
  3. Each moment has a *state*: the small image of what it currently shows. A frame that matches
     the state (difference < ``same_frac``) extends the moment. A frame that does not starts a
     *pending* state; it is accepted once frames keep matching it for ``settle_s`` seconds.
     Transition / animation frames never settle, so they are never accepted or picked.
  4. An accepted state either *updates* the current moment or starts a new one:
       - near-duplicate (difference < ``dup_frac``): update.
       - slide build: if ``merge_builds`` (default) and the change only *adds* content (the old
         state's edges survive in the new one: a bullet appears, a whiteboard stroke is drawn),
         update. The moment's representative becomes the latest, most complete state.
       - anything else (new slide, app/tab switch, scroll, something covered or erased): a new
         moment, with ``changed_region`` = bounding box of what changed vs the previous moment
         (None when the change spans most of the frame).
  5. A new moment is only a *candidate* until it has lasted ``min_moment_s``. If the screen goes
     back to the current moment first (a brief flash to another slide or a menu), the candidate is
     discarded. If another state settles first, the candidate is dropped and its time folded in.
     So a moment is returned by ``feed`` when its successor has proven itself.
  6. The representative frame is the sharpest (Laplacian variance) real frame of the moment's
     latest settled state.
  7. Each moment records its *builds*: the first settled state, then every additive update, with
     the region that was added. A worked example written line by line becomes one moment whose
     builds are the lines, so notes can follow it step by step.
  8. Scrolling a canvas (a note app, a long document) moves content without changing it. If the
     old state's content reappears shifted vertically, still on screen, it is the same moment: a
     build whose region is whatever newly came into view, with earlier build regions shifted to
     match. Static parts (toolbars) are ignored when matching. Content scrolled off screen counts
     as lost, so a long scroll still starts a new moment.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from iris.models import Box, Build, EventType, FrameIndex, FrameRef, Moment, Moments
from iris.pipeline.context import StageContext
from iris.store.layout import read_model, write_model


@dataclass(frozen=True)
class DetectParams:
    """Tunables. Fractions are of the work image's pixels; times are in seconds."""

    # Preprocessing
    work_width: int = 320  # frames are downscaled to this width before comparing
    blur_ksize: int = 5  # Gaussian blur kernel (odd) applied after downscaling
    pixel_threshold: int = 25  # grey-level difference (0-255) for a pixel to count as changed

    # Change thresholds
    same_frac: float = 0.005  # below this, a frame shows the same thing (cursor, caret, noise)
    dup_frac: float = 0.01  # below this, a settled change is a near-duplicate: update, not new

    # Settling and duration
    settle_s: float = 2.0  # a new state must hold this long to count as settled
    min_settle_frames: int = 2  # ...and over at least this many frames
    min_moment_s: float = 4.0  # shorter moments are folded into their neighbours

    # Slide builds
    merge_builds: bool = True  # purely additive changes update the moment instead of a new one
    additive_keep: float = 0.85  # share of the old state's edges that must survive to be additive

    # Masking of persistently changing regions (thumbnails, embedded video)
    activity_decay: float = 0.8  # per-frame decay of the per-pixel activity average
    activity_threshold: float = 0.6  # activity above this is masked (about 5 changes in a row)
    max_mask_frac: float = 0.35  # no masking when it would cover more than this of the frame

    # Changed region
    region_dilate: int = 9  # work-pixel dilation that merges nearby changes (text) into blobs
    region_min_blob_frac: float = 0.0005  # ignore blobs whose bounding box is smaller than this
    region_max_frac: float = 0.6  # a region larger than this is reported as None (full screen)
    region_pad: int = 8  # padding added around the region, in frame pixels

    # Scrolling
    scroll_max_frac: float = 0.6  # largest vertical scroll considered, as a share of the height
    scroll_min_edges: int = 40  # moved content needed (work-image edge pixels) to judge a scroll


@dataclass
class _State:
    """What a moment (or a candidate moment) currently shows."""

    start: float
    small: np.ndarray
    edges: np.ndarray
    frame: FrameRef
    sharpness: float
    last_t: float
    mask_frac: float  # share of the frame masked when this state settled
    region: Box | None = None
    builds: list[Build] = field(default_factory=list)

    def consider(self, t: float, ref: FrameRef, sharpness: float, can_represent: bool) -> None:
        self.last_t = t
        if can_represent and sharpness > self.sharpness:
            self.frame, self.sharpness = ref, sharpness

    def replace_content(self, other: "_State", build: Build | None = None) -> None:
        """Take the newer state's content and representative, keeping start and region."""
        if build is not None:
            self.builds.append(build)
        self.small, self.edges = other.small, other.edges
        self.frame, self.sharpness = other.frame, other.sharpness
        self.last_t, self.mask_frac = other.last_t, other.mask_frac


@dataclass
class _Pending:
    """Frames that differ from the current state and may be settling on something new."""

    onset: float  # when the screen first departed from the current state
    small: np.ndarray
    frames: list[tuple[float, FrameRef, float]] = field(default_factory=list)


_OPEN_KERNEL = np.ones((3, 3), np.uint8)
_MASK_KERNEL = np.ones((5, 5), np.uint8)


class MomentDetector:
    """Incremental moment detector. ``feed`` frames in time order, then call ``finish`` once."""

    def __init__(self, params: DetectParams | None = None):
        self.p = params or DetectParams()
        self._current: _State | None = None  # the open moment
        self._next: _State | None = None  # a new moment that has not lasted min_moment_s yet
        self._pending: _Pending | None = None
        self._fallback: tuple[FrameRef, float] | None = None
        self._first_t: float | None = None
        self._work_size: tuple[int, int] | None = None  # (w, h) of work images
        self._frame_size: tuple[int, int] = (0, 0)  # (w, h) of the latest input frame
        self._prev_small: np.ndarray | None = None
        self._activity: np.ndarray | None = None
        self._mask: np.ndarray | None = None  # True where pixels are ignored
        self._count = 0

    # --- public API ----------------------------------------------------------------------------

    def feed(self, t: float, frame_ref: FrameRef, image: np.ndarray) -> Moment | None:
        """Add the next frame. Returns a moment when one closes (its successor has settled)."""
        small = self._prepare(image)
        self._update_mask(small)
        sharpness = _sharpness(image)
        if self._first_t is None:
            self._first_t = t

        if (
            self._current is None
            and self._count == 0
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
            # Back to what was on screen before the candidate proved itself: a brief flash.
            self._next = self._pending = None
            self._current.consider(t, frame_ref, sharpness, self._can_represent(self._current))
        else:
            self._add_pending(t, frame_ref, small, sharpness)
            if self._is_settled():
                self._accept()
        return self._promote()

    def finish(self, end_t: float) -> Moment | None:
        """Close the last moment; use a real frame if no state ever settled.

        The fallback is a candidate for downstream selection, not evidence of a stable screen.
        An unsettled tail after an established moment still belongs to that moment.
        """
        current, self._current = self._current, None
        fallback, self._fallback = self._fallback, None
        pending = self._pending
        self._next = self._pending = None  # an unproven candidate is too short to stand alone
        if current is None:
            if self._count == 0 and fallback is not None and pending is not None:
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

    # --- frame processing ----------------------------------------------------------------------

    def _prepare(self, image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        h, w = gray.shape
        self._frame_size = (w, h)
        if self._work_size is None:
            ww = min(self.p.work_width, w)
            self._work_size = (ww, max(1, round(h * ww / w)))
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
        # A growing mask means something is moving that was not when the state settled (e.g. a
        # panel being scrolled); do not pick such frames as the representative.
        return self._mask_frac() <= state.mask_frac + 0.02

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

    # --- moment state machine ------------------------------------------------------------------

    def _accept(self) -> None:
        """The pending frames have settled: update the current moment or start a candidate."""
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
            # The candidate never lasted min_moment_s; the new one takes over its time.
            state.start = self._next.start
        state.region = self._region(self._changed(self._current.small, state.small))
        state.builds = [Build(t=round(settled_t, 3), frame=ref, region=state.region)]
        self._next = state

    def _absorbs(self, old: _State, new: _State, settled_t: float) -> Build | None | bool:
        """Whether a newly settled state is the same moment as ``old``.

        Returns False for a different moment, None for a near-duplicate, and the ``Build`` for an
        additive change.
        """
        changed = self._changed(old.small, new.small)
        small_change = cv2.countNonZero(changed) / changed.size < self.p.dup_frac
        if (self.p.merge_builds or small_change) and self._is_additive(old, new, changed):
            # Handwriting adds little ink per line, so even small additions are builds.
            return Build(t=round(settled_t, 3), frame=new.frame, region=self._region(changed))
        if small_change:
            return None
        if self.p.merge_builds and (dy := self._scroll(old, new)) is not None:
            return self._scroll_build(old, new, dy, settled_t)
        return False

    def _scroll(self, old: _State, new: _State) -> int | None:
        """Vertical shift (work pixels) that carries all of ``old``'s content onto ``new``."""
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
            # Content pushed off screen counts against the match: it is no longer visible.
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
        # Earlier builds' regions refer to positions before the scroll; move them with it.
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
        """True when the change only adds content: old edges in the changed area survive."""
        area = cv2.dilate(changed, _MASK_KERNEL).astype(bool)
        old_edges = old.edges & area
        n = int(old_edges.sum())
        if n < 20:  # the change landed on empty background
            return True
        new_edges = cv2.dilate(new.edges.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        return (old_edges & new_edges).sum() / n >= self.p.additive_keep

    def _promote(self) -> Moment | None:
        """Close the current moment once its candidate successor has lasted ``min_moment_s``."""
        nxt = self._next
        if nxt is None or nxt.last_t - nxt.start < self.p.min_moment_s:
            return None
        current, self._current, self._next = self._current, nxt, None
        assert current is not None
        if nxt.start - current.start < self.p.min_moment_s:
            # Only possible for the first moment (later ones lasted min_moment_s to get here).
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
        """Bounding box (frame pixels) of the changed blobs, or None if it spans most of it."""
        k = self.p.region_dilate
        blobs = cv2.dilate(changed, np.ones((k, k), np.uint8))
        contours, _ = cv2.findContours(blobs, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        min_area = self.p.region_min_blob_frac * blobs.size
        rects = [r for r in map(cv2.boundingRect, contours) if r[2] * r[3] >= min_area]
        if not rects:
            return None
        # Dilation grew each blob by k // 2 on every side; undo that before scaling.
        g = k // 2
        x0 = min(r[0] for r in rects) + g
        y0 = min(r[1] for r in rects) + g
        x1 = max(r[0] + r[2] for r in rects) - g
        y1 = max(r[1] + r[3] for r in rects) - g
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
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    h, w = gray.shape
    if w > 640:
        gray = cv2.resize(gray, (640, max(1, round(h * 640 / w))), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


# --- stage -------------------------------------------------------------------------------------


def detect_moments(
    root: Path,
    index: FrameIndex,
    params: DetectParams | None = None,
    on_moment: Callable[[Moment], None] | None = None,
) -> list[Moment]:
    """Run the detector over a session's frames (blocking; call in a thread)."""
    detector = MomentDetector(params)
    moments: list[Moment] = []

    def keep(m: Moment | None) -> None:
        if m is not None:
            moments.append(m)
            if on_moment is not None:
                on_moment(m)

    for ref in index.frames:
        image = cv2.imread(str(root / ref.path), cv2.IMREAD_COLOR)
        if image is None:
            continue  # unreadable frame: skip rather than fail the session
        keep(detector.feed(ref.t, ref, image))
    if index.frames:
        step = 1.0 / index.fps if index.fps > 0 else 0.0
        keep(detector.finish(index.frames[-1].t + step))
    return moments


def moment_event(m: Moment) -> dict:
    """The MOMENT_FOUND event data for a moment (also emitted by the live screen lane)."""
    return {"id": m.id, "start": m.start, "end": m.end, "frame": m.frame.path}


async def run(ctx: StageContext, params: DetectParams | None = None) -> None:
    index = read_model(ctx.paths.frame_index, FrameIndex)
    loop = asyncio.get_running_loop()

    def on_moment(m: Moment) -> None:
        # Detection runs in a worker thread; deliver events on the event loop.
        loop.call_soon_threadsafe(ctx.emit, EventType.MOMENT_FOUND, moment_event(m))

    moments = await asyncio.to_thread(detect_moments, ctx.paths.root, index, params, on_moment)
    write_model(ctx.paths.moments, Moments(moments=moments))
