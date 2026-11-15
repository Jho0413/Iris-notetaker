"""Upload source: a recording file saved as ``source.<ext>``.

Ingest decodes it with PyAV (whose wheels bundle FFmpeg, so there is no system install and no
subprocess) into the standard session layout: ``audio.wav`` and ``frames/`` + ``frames.json``.

All timestamps are seconds from the session start, which is the container's start time (the
earliest stream start), so audio and frames share one clock even when a stream starts late.
"""

import asyncio
import math
import shutil
import wave
from pathlib import Path
from typing import BinaryIO

import av
import cv2
import numpy as np
from av.container import InputContainer

from iris.llm.errors import UserFacingError
from iris.models import UNREADABLE_UPLOAD, UPLOAD_WITHOUT_AUDIO, FrameIndex, FrameRef
from iris.pipeline.context import StageContext
from iris.store.layout import SessionPaths, frame_name, frame_path, write_model

JPEG_QUALITY = 90
# Tolerance when comparing a frame time to the sampling grid, so a frame stamped 0.9999 s by
# rounding still counts as the 1 s sample.
_GRID_EPS = 1e-3


def save_upload(paths: SessionPaths, filename: str, data: BinaryIO) -> Path:
    ext = Path(filename).suffix.lower() or ".bin"
    dest = paths.source(ext)
    with dest.open("wb") as out:
        shutil.copyfileobj(data, out)
    return dest


class UploadSource:
    async def ingest(self, ctx: StageContext) -> None:
        """Decode the upload into audio.wav and sampled frames, faster than real time.

        Audio and frames are decoded concurrently in worker threads (decoding is CPU bound and
        FFmpeg releases the GIL). A file that can't be opened or decoded, or has no audio, is the
        user's to fix: ``UserFacingError``.
        """
        src = ctx.paths.find_source()
        if src is None:
            raise FileNotFoundError("no source.* file in session")
        check_has_audio(src)  # fail fast, before spending time decoding video
        s = ctx.settings
        try:
            await asyncio.gather(
                asyncio.to_thread(extract_audio, src, ctx.paths.audio, s.audio_sample_rate),
                asyncio.to_thread(ingest_frames, src, ctx.paths, s.frame_fps, s.frame_max_width),
            )
        except av.FFmpegError as e:  # damaged or truncated past what the decoder can skip
            raise UserFacingError(UNREADABLE_UPLOAD, f"{src.name} could not be decoded: {e}") from e


# --- Opening ----------------------------------------------------------------------------------


def open_media(src: Path) -> InputContainer:
    try:
        return av.open(str(src))
    except av.FFmpegError as e:
        raise UserFacingError(
            UNREADABLE_UPLOAD, f"{src.name} is not a readable audio or video file: {e}"
        ) from e


def session_origin(container: InputContainer) -> float:
    """Session start in seconds: the earliest start time over all streams."""
    if container.start_time is None:
        return 0.0
    return container.start_time / av.time_base


def first_video_stream(container: InputContainer) -> av.VideoStream | None:
    """The first real video stream, ignoring cover art embedded in audio files."""
    for stream in container.streams.video:
        if not stream.disposition & av.stream.Disposition.attached_pic:
            return stream
    return None


def check_has_audio(src: Path) -> None:
    with open_media(src) as container:
        if not container.streams.audio:
            raise UserFacingError(UPLOAD_WITHOUT_AUDIO, f"{src.name} has no audio track")


# --- Audio ------------------------------------------------------------------------------------


def extract_audio(src: Path, dest: Path, rate: int) -> float:
    """Decode the first audio stream to 16-bit mono PCM WAV at ``rate`` Hz.

    If the audio starts after the session origin (another stream starts earlier), leading
    silence is written so that audio time 0 is session time 0. Written to a temp file and
    renamed, so ``dest`` is never half-written. Returns the duration in seconds.
    """
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open_media(src) as container:
        if not container.streams.audio:
            raise UserFacingError(UPLOAD_WITHOUT_AUDIO, f"{src.name} has no audio track")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        origin = session_origin(container)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        n_samples = 0
        with wave.open(str(tmp), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            for frame in container.decode(stream):
                if n_samples == 0 and frame.time is not None:
                    lead = round((frame.time - origin) * rate)
                    if lead > 0:
                        out.writeframes(bytes(2 * lead))
                        n_samples += lead
                for chunk in resampler.resample(frame):
                    n_samples += _write_pcm(out, chunk)
            for chunk in resampler.resample(None):  # flush
                n_samples += _write_pcm(out, chunk)
    tmp.replace(dest)
    return n_samples / rate


def _write_pcm(out: wave.Wave_write, frame: av.AudioFrame) -> int:
    pcm = frame.to_ndarray()  # (1, samples) int16 for packed mono s16
    out.writeframes(pcm.tobytes())
    return frame.samples


# --- Frames -----------------------------------------------------------------------------------


def ingest_frames(src: Path, paths: SessionPaths, fps: float, max_width: int) -> FrameIndex:
    """Replace any previous frames with fresh ones and write ``frames.json`` last.

    ``frames.json`` is the commit point: it is removed first and written atomically at the end,
    so if it exists, every frame it lists is on disk.
    """
    paths.frame_index.unlink(missing_ok=True)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    for old in paths.frames_dir.glob("*.jpg"):
        old.unlink()
    index = extract_frames(src, paths.frames_dir, fps, max_width)
    write_model(paths.frame_index, index)
    return index


def extract_frames(src: Path, frames_dir: Path, fps: float, max_width: int) -> FrameIndex:
    """Save one frame every ``1/fps`` s of presentation time as ``frames/<t_ms:09d>.jpg``.

    Every frame must be decoded (inter-frame codecs need their references), but only the kept
    frames are scaled, colour-converted and encoded, which is where most of the cost would be.
    A frame is kept when its time reaches the next point on the ``k/fps`` grid, so variable
    frame rate input and gaps are handled by timestamps, not frame counts. Frames are rotated
    upright per the stream's display matrix and scaled to at most ``max_width`` wide; all frames
    share the size of the first one. Audio-only input gives an empty index.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    refs: list[FrameRef] = []
    size: tuple[int, int] | None = None  # (width, height) as saved
    with open_media(src) as container:
        stream = first_video_stream(container)
        if stream is None:
            return FrameIndex(fps=fps, width=0, height=0, frames=[])
        stream.thread_type = "AUTO"
        origin = session_origin(container)
        next_k = 0
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            t = max(0.0, frame.time - origin)
            if t * fps + _GRID_EPS < next_k:
                continue
            next_k = math.floor(t * fps + _GRID_EPS) + 1
            rotation = frame.rotation % 360
            if size is None:
                size = output_size(frame.width, frame.height, rotation, max_width)
            image = to_upright_bgr(frame, size, rotation)
            name = frame_name(t)
            if not cv2.imwrite(
                str(frames_dir / name), image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
            ):
                raise OSError(f"could not write frame {name}")
            refs.append(FrameRef(t=t, path=frame_path(t)))
    width, height = size or (0, 0)
    return FrameIndex(fps=fps, width=width, height=height, frames=refs)


def output_size(width: int, height: int, rotation: int, max_width: int) -> tuple[int, int]:
    """Upright (width, height) scaled down to at most ``max_width`` wide, keeping aspect."""
    if rotation in (90, 270):
        width, height = height, width
    if width <= max_width:
        return width, height
    return max_width, max(1, round(height * max_width / width))


def to_upright_bgr(frame: av.VideoFrame, size: tuple[int, int], rotation: int) -> np.ndarray:
    """Scale and convert in one swscale pass, then rotate (cheaper on the smaller image)."""
    width, height = size
    if rotation in (90, 270):
        width, height = height, width
    image = frame.reformat(
        width=width, height=height, format="bgr24", interpolation="AREA"
    ).to_ndarray()
    # frame.rotation is the counterclockwise angle needed to display the frame upright.
    if rotation == 90:
        return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if rotation == 180:
        return cv2.rotate(image, cv2.ROTATE_180)
    if rotation == 270:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    return image
