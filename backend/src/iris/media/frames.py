import logging
import math
import shutil
from collections.abc import Callable
from pathlib import Path

import av
import cv2
import numpy as np

from iris.media.images import size_within_width
from iris.models import FrameIndex, FrameRef
from iris.store.layout import SessionPaths, frame_name, frame_path, write_model

log = logging.getLogger(__name__)

JPEG_QUALITY = 90
_GRID_EPS = 1e-3


class GridSampler:
    def __init__(self, fps: float):
        self.fps = fps
        self._next_k = 0

    def cell(self, t: float) -> int:
        return math.floor(t * self.fps + _GRID_EPS)

    def take(self, t: float) -> bool:
        if t * self.fps + _GRID_EPS < self._next_k:
            return False
        self._next_k = self.cell(t) + 1
        return True


def output_size(width: int, height: int, rotation: int, max_width: int) -> tuple[int, int]:
    if rotation in (90, 270):
        width, height = height, width
    return size_within_width(width, height, max_width)


def to_upright_bgr(frame: av.VideoFrame, size: tuple[int, int], rotation: int) -> np.ndarray:
    width, height = size
    if rotation in (90, 270):
        width, height = height, width
    image = frame.reformat(
        width=width, height=height, format="bgr24", interpolation="AREA"
    ).to_ndarray()
    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image


def fit(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    width, height = size
    h, w = image.shape[:2]
    scale = min(width / w, height / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    scaled = cv2.resize(image, (new_w, new_h), interpolation=interp)
    canvas = np.zeros((height, width, 3), np.uint8)
    x, y = (width - new_w) // 2, (height - new_h) // 2
    canvas[y : y + new_h, x : x + new_w] = scaled
    return canvas


def clear_frames(paths: SessionPaths) -> None:
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    for old in paths.frames_dir.glob("*.jpg"):
        old.unlink()


def rebuild_frames(paths: SessionPaths, write_frames: Callable[[], FrameIndex]) -> FrameIndex:
    paths.frame_index.unlink(missing_ok=True)
    clear_frames(paths)
    index = write_frames()
    write_model(paths.frame_index, index)
    return index


def write_frame(frames_dir: Path, t: float, image: np.ndarray) -> FrameRef:
    name = frame_name(t)
    if not cv2.imwrite(str(frames_dir / name), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
        raise OSError(f"could not write frame {name}")
    return FrameRef(t=t, path=frame_path(t))


class FrameNormaliser:
    def __init__(self, frames_dir: Path, max_width: int):
        self.frames_dir = frames_dir
        self.max_width = max_width
        self.size: tuple[int, int] | None = None
        self.first_size: tuple[int, int] | None = None

    def new_share(self) -> None:
        self.size = None

    def add(self, t: float, src: Path) -> FrameRef | None:
        image = _read_captured_frame(src)
        if image is None:
            return None
        if self.size is None:
            height, width = image.shape[:2]
            self.size = output_size(width, height, 0, self.max_width)
            self.first_size = self.first_size or self.size
        if (image.shape[1], image.shape[0]) == self.size:
            return self._copy_without_reencoding(t, src)
        return write_frame(self.frames_dir, t, fit(image, self.size))

    def _copy_without_reencoding(self, t: float, src: Path) -> FrameRef:
        shutil.copyfile(src, self.frames_dir / frame_name(t))
        return FrameRef(t=t, path=frame_path(t))


def _read_captured_frame(src: Path) -> np.ndarray | None:
    image = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if image is None:
        log.warning("skipping unreadable live frame %s", src.name)
    return image
