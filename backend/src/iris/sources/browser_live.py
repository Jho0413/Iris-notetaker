"""Browser live source: audio chunks and screen frames posted by the UI while recording.

Capture (while recording, through ``api/routes/live.py``):
  - Audio: MediaRecorder timeslice chunks, stored as ``live/audio/<seq:06d>.webm``. The chunks are
    consecutive byte ranges of one WebM stream (only chunk 0 has the header), so concatenated in
    ``seq`` order they form a valid stream whose timestamps start at 0 when recording started.
  - Frames: JPEG screenshots, stored as ``live/frames/<t_ms:09d>.jpg``, where ``t`` is seconds
    since recording started (the same clock as the audio).

  Chunks are keyed by ``seq`` and frames by ``t``, so chunks arriving out of order land in the
  right place and a chunk posted twice (a client retry) is stored once. Each file is written
  atomically. Ingest never modifies the captured files, so a session can be re-ingested (retried)
  at any time, and the session stays its own recording.

Ingest (when recording ends) writes the standard layout, exactly like the upload source:
  - ``audio.wav``: the chunks decoded in order as 16 kHz mono. Audio is placed by its timestamps,
    so a missing chunk becomes silence of the same length rather than pulling later speech
    earlier, which keeps audio and screen aligned.
  - ``frames/`` + ``frames.json``: captured frames sampled onto the ``frame_fps`` grid, scaled to
    at most ``frame_max_width`` wide and letterboxed to one size, because detection compares
    frames pixel by pixel and a shared window can change size mid-session.
"""

import asyncio
import io
import logging
import math
import re
import shutil
import wave
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO

import av
import cv2
import numpy as np

from iris.models import FrameIndex, FrameRef
from iris.pipeline.context import StageContext
from iris.sources.upload import JPEG_QUALITY, output_size
from iris.store.layout import SessionPaths, write_model

log = logging.getLogger(__name__)

# Chunk and frame names have fixed widths: 6 digits of seq, 9 digits of milliseconds.
MAX_AUDIO_SEQ = 999_999  # 57 days of 5 s chunks
MAX_FRAME_T = 24 * 3600.0  # seconds; well inside the 9-digit millisecond name
MAX_UPLOAD_BYTES = 16 * 1024 * 1024  # per chunk or frame; real ones are well under 1 MB

WEBM_MAGIC = b"\x1a\x45\xdf\xa3"  # EBML header, at the start of chunk 0 only
JPEG_MAGIC = b"\xff\xd8\xff"

# A forward jump in audio timestamps longer than this is a gap (a lost chunk) and is filled with
# silence; shorter ones are codec and resampler jitter.
AUDIO_GAP_S = 0.05
# Tolerance when comparing a frame time to the sampling grid (as in the upload source).
_GRID_EPS = 1e-3
# Frames are decoded, scaled and re-encoded in parallel (OpenCV releases the GIL).
FRAME_WORKERS = 4

_AUDIO_NAME = re.compile(r"^(\d{6})\.webm$")
_FRAME_NAME = re.compile(r"^(\d{9})\.jpg$")


class ChunkConflict(Exception):
    """A chunk or frame with this key was already stored with different content."""


# --- Capture ----------------------------------------------------------------------------------


def frame_name(t: float) -> str:
    return f"{round(t * 1000):09d}.jpg"


def audio_chunk_path(paths: SessionPaths, seq: int) -> Path:
    return paths.live_audio_dir / f"{seq:06d}.webm"


def store_audio_chunk(paths: SessionPaths, seq: int, data: bytes) -> bool:
    """Store audio chunk ``seq``. Returns False if this exact chunk was already stored."""
    return _store_once(audio_chunk_path(paths, seq), data)


def store_frame(paths: SessionPaths, t: float, data: bytes) -> bool:
    """Store a frame captured ``t`` s after recording started. False if already stored."""
    return _store_once(paths.live_frames_dir / frame_name(t), data)


def _store_once(path: Path, data: bytes) -> bool:
    """Write ``data`` atomically unless ``path`` exists. A repeat with the same bytes is a no-op;
    different bytes raise ``ChunkConflict`` rather than silently replacing captured content."""
    if path.exists():
        if path.read_bytes() == data:
            return False
        raise ChunkConflict(f"{path.name} was already received with different content")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def audio_chunks(paths: SessionPaths) -> list[tuple[int, Path]]:
    """Stored audio chunks as ``(seq, path)``, in ``seq`` order."""
    if not paths.live_audio_dir.is_dir():
        return []
    found = []
    for p in paths.live_audio_dir.iterdir():
        if m := _AUDIO_NAME.match(p.name):
            found.append((int(m.group(1)), p))
    return sorted(found)


def missing_audio_chunks(paths: SessionPaths, expected: int) -> list[int]:
    """Sequence numbers in ``0..expected-1`` that have not been stored."""
    have = {seq for seq, _ in audio_chunks(paths)}
    return [seq for seq in range(expected) if seq not in have]


def captured_frames(paths: SessionPaths) -> list[tuple[float, Path]]:
    """Stored frames as ``(t, path)``, in time order."""
    if not paths.live_frames_dir.is_dir():
        return []
    found = []
    for p in paths.live_frames_dir.iterdir():
        if m := _FRAME_NAME.match(p.name):
            found.append((int(m.group(1)) / 1000, p))
    return sorted(found)


# --- Ingest -----------------------------------------------------------------------------------


class BrowserLiveSource:
    async def ingest(self, ctx: StageContext) -> None:
        """Finalize the captured chunks and frames into ``audio.wav`` and ``frames.json``."""
        chunks = audio_chunks(ctx.paths)
        if not chunks:
            raise ValueError("no audio was recorded")
        if chunks[0][0] != 0:
            raise ValueError("the first audio chunk (the stream header) is missing")
        missing = missing_audio_chunks(ctx.paths, chunks[-1][0] + 1)
        if missing:
            # The end request already refused gaps unless the client accepted them.
            log.warning(
                "session %s: audio chunks %s missing; filled with silence", ctx.session_id, missing
            )
        s = ctx.settings
        await asyncio.gather(
            asyncio.to_thread(finalize_audio, chunks, ctx.paths.audio, s.audio_sample_rate),
            asyncio.to_thread(finalize_frames, ctx.paths, s.frame_fps, s.frame_max_width),
        )


# --- Audio ------------------------------------------------------------------------------------


# The Matroska/WebM Cluster element ID. Every cluster carries an absolute timecode, so decoding
# can restart at one after a lost chunk, given the stream header (EBML, Segment info and Tracks).
CLUSTER_ID = b"\x1f\x43\xb6\x75"
MAX_HEADER_BYTES = 1024 * 1024  # MediaRecorder headers are a few hundred bytes

# A part of a stream: literal bytes, or a file read from an offset.
Part = bytes | tuple[Path, int]


class ChunkReader(io.RawIOBase):
    """Reads parts one after another as one stream, without joining them on disk."""

    def __init__(self, parts: Sequence[Part]):
        self._parts = list(parts)
        self._next = 0
        self._current: io.BufferedIOBase | None = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[override]
        while True:
            if self._current is None:
                if self._next >= len(self._parts):
                    return 0
                part = self._parts[self._next]
                self._next += 1
                if isinstance(part, bytes):
                    self._current = io.BytesIO(part)
                else:
                    self._current = part[0].open("rb")
                    self._current.seek(part[1])
            n = self._current.readinto(buffer)
            if n:
                return n
            self._current.close()
            self._current = None

    def close(self) -> None:
        if self._current is not None:
            self._current.close()
            self._current = None
        super().close()


def contiguous_runs(chunks: Sequence[tuple[int, Path]]) -> list[list[Path]]:
    """Group ``(seq, path)`` chunks (in order) into runs with no missing ``seq`` inside."""
    runs: list[list[Path]] = []
    prev = None
    for seq, path in chunks:
        if prev is None or seq != prev + 1:
            runs.append([])
        runs[-1].append(path)
        prev = seq
    return runs


def find_cluster(files: Sequence[Path], limit: int | None = None) -> tuple[int, int] | None:
    """``(file index, offset)`` of the first Cluster element in ``files`` read as one stream.

    Matches across file boundaries. Stops after ``limit`` bytes if given.
    """
    keep = len(CLUSTER_ID) - 1
    carry = b""
    scanned = 0
    prev_len = 0
    for i, path in enumerate(files):
        data = path.read_bytes()
        pos = (carry + data).find(CLUSTER_ID)
        if pos >= 0:
            if pos < len(carry):
                return i - 1, prev_len - len(carry) + pos
            return i, pos - len(carry)
        scanned += len(data)
        if limit is not None and scanned >= limit:
            return None
        carry = (carry + data)[-keep:]
        prev_len = len(data)
    return None


def stream_header(files: Sequence[Path]) -> bytes | None:
    """The bytes before the first cluster: what a stream needs to decode a later cluster.

    If ``files`` hold no complete cluster (MediaRecorder's first chunk can be header only), all of
    them are the header, minus any partial Cluster ID at the end. None if that is implausibly
    large for a header.
    """
    found = find_cluster(files, MAX_HEADER_BYTES)
    if found is not None:
        index, offset = found
        return b"".join(p.read_bytes() for p in files[:index]) + files[index].read_bytes()[:offset]
    if sum(p.stat().st_size for p in files) > MAX_HEADER_BYTES:
        return None
    data = b"".join(p.read_bytes() for p in files)
    for k in range(len(CLUSTER_ID) - 1, 0, -1):
        if data.endswith(CLUSTER_ID[:k]):
            return data[:-k]
    return data


def finalize_audio(chunks: Sequence[tuple[int, Path]], dest: Path, rate: int) -> float:
    """Decode the ``(seq, path)`` chunks, in order, to 16-bit mono PCM WAV at ``rate`` Hz.

    The first contiguous run of chunks is one stream. After each missing chunk, decoding restarts
    at the next cluster with the stream header prepended (rather than trusting the demuxer to
    resynchronise inside damaged data, which can lose the rest of the recording). Samples are
    placed by timestamp (``PlacedWav``), so audio time stays session time. A damaged packet is
    skipped. Written to a temp file and renamed, so ``dest`` is never half-written. Returns the
    duration in seconds.
    """
    runs = contiguous_runs(chunks)
    if not runs:
        raise ValueError("no audio was recorded")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with wave.open(str(tmp), "wb") as out:
            wav = PlacedWav(out, rate)
            # A first run that can't be opened is only fatal if nothing follows it.
            _decode_run([(p, 0) for p in runs[0]], wav, required=len(runs) == 1)
            header = stream_header(runs[0]) if len(runs) > 1 else None
            for run in runs[1:]:
                found = find_cluster(run)
                if header is None or found is None:
                    log.warning("no decodable cluster after a gap; %d chunks skipped", len(run))
                    continue
                index, offset = found
                rest = [(p, 0) for p in run[index + 1 :]]
                _decode_run([header, (run[index], offset), *rest], wav, required=False)
            wav.flush()
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if wav.samples == 0:
        tmp.unlink(missing_ok=True)
        raise ValueError("the live recording contains no audio")
    tmp.replace(dest)
    return wav.samples / rate


class RawPcm:
    """A ``PlacedWav`` destination that writes bare samples (no WAV header) to a binary file: the
    live audio lane's ``live/audio.pcm``, which grows as chunks are decoded."""

    def __init__(self, file: BinaryIO):
        self.file = file

    def setnchannels(self, n: int) -> None:
        pass

    def setsampwidth(self, n: int) -> None:
        pass

    def setframerate(self, rate: int) -> None:
        pass

    def writeframes(self, data: bytes) -> None:
        self.file.write(data)


class PlacedWav:
    """Writes decoded audio as mono s16 at ``rate``, placed by timestamp.

    Leading audio and any gap longer than ``AUDIO_GAP_S`` are written as silence. Before a gap
    the resampler is flushed, so audio it still holds from before the gap is not played after it.
    """

    def __init__(self, out: wave.Wave_write | RawPcm, rate: int):
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        self.out = out
        self.rate = rate
        self.samples = 0
        self._gap = round(AUDIO_GAP_S * rate)
        self._resampler: av.AudioResampler | None = None

    def write(self, frame: av.AudioFrame) -> None:
        if frame.time is not None and round(frame.time * self.rate) - self.samples > self._gap:
            self.flush()
            lead = round(frame.time * self.rate) - self.samples
            if lead > 0:
                self.out.writeframes(bytes(2 * lead))
                self.samples += lead
        if self._resampler is None:
            self._resampler = av.AudioResampler(format="s16", layout="mono", rate=self.rate)
        for chunk in self._resampler.resample(frame):
            self._write_pcm(chunk)

    def flush(self) -> None:
        if self._resampler is not None:
            for chunk in self._resampler.resample(None):
                self._write_pcm(chunk)
            self._resampler = None

    def _write_pcm(self, frame: av.AudioFrame) -> None:
        self.out.writeframes(frame.to_ndarray().tobytes())  # (1, samples) int16, packed mono
        self.samples += frame.samples


def _decode_run(parts: Sequence[Part], wav: PlacedWav, *, required: bool) -> None:
    """Decode one stream (the first run, or the header plus a later run) into ``wav``.

    If the stream can't be opened, raise when ``required``, else log and skip it.
    """
    with ChunkReader(parts) as reader:
        try:
            container = av.open(reader)
        except av.FFmpegError as e:
            if required:
                raise ValueError("the live audio is not a readable recording") from e
            log.warning("skipping live audio that could not be opened: %s", e)
            return
        with container:
            if not container.streams.audio:
                raise ValueError("the live recording has no audio track")
            for packet in container.demux(container.streams.audio[0]):
                try:
                    frames = packet.decode()
                except av.FFmpegError as e:
                    log.warning("skipping a damaged live audio packet: %s", e)
                    continue
                for frame in frames:
                    wav.write(frame)


# --- Frames -----------------------------------------------------------------------------------


def finalize_frames(paths: SessionPaths, fps: float, max_width: int) -> FrameIndex:
    """Rebuild ``frames/`` from the captured frames and write ``frames.json`` last.

    As in the upload source, ``frames.json`` is the commit point: it is removed first and written
    atomically at the end. Frames are sampled onto the ``k/fps`` grid by timestamp (the browser
    sends about one per second, unevenly). Unreadable frames are skipped. No frames (audio-only
    capture) gives an empty index.
    """
    paths.frame_index.unlink(missing_ok=True)
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    for old in paths.frames_dir.glob("*.jpg"):
        old.unlink()

    picked = _sample_on_grid(captured_frames(paths), fps)
    size: tuple[int, int] | None = None  # (width, height), from the first readable frame
    first = 0
    while size is None and first < len(picked):
        image = cv2.imread(str(picked[first][1]), cv2.IMREAD_COLOR)
        if image is None:
            log.warning("skipping unreadable live frame %s", picked[first][1].name)
            first += 1
            continue
        height, width = image.shape[:2]
        size = output_size(width, height, 0, max_width)

    refs: list[FrameRef] = []
    if size is not None:
        target = size

        def convert(item: tuple[float, Path]) -> FrameRef | None:
            return _convert_frame(item[0], item[1], paths.frames_dir, target)

        with ThreadPoolExecutor(FRAME_WORKERS) as pool:
            refs = [ref for ref in pool.map(convert, picked[first:]) if ref is not None]
    width, height = size or (0, 0)
    index = FrameIndex(fps=fps, width=width, height=height, frames=refs)
    write_model(paths.frame_index, index)
    return index


def _sample_on_grid(frames: list[tuple[float, Path]], fps: float) -> list[tuple[float, Path]]:
    """The first frame at or after each ``k/fps`` grid point that has frames."""
    picked = []
    next_k = 0
    for t, path in frames:
        if t * fps + _GRID_EPS < next_k:
            continue
        next_k = math.floor(t * fps + _GRID_EPS) + 1
        picked.append((t, path))
    return picked


def _convert_frame(t: float, src: Path, frames_dir: Path, size: tuple[int, int]) -> FrameRef | None:
    name = frame_name(t)
    image = cv2.imread(str(src), cv2.IMREAD_COLOR)
    if image is None:
        log.warning("skipping unreadable live frame %s", src.name)
        return None
    if (image.shape[1], image.shape[0]) == size:
        shutil.copyfile(src, frames_dir / name)  # already the right size: no re-encode loss
    elif not cv2.imwrite(
        str(frames_dir / name), fit(image, size), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
    ):
        raise OSError(f"could not write frame {name}")
    return FrameRef(t=t, path=f"frames/{name}")


def fit(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Scale ``image`` to fit ``size`` (width, height) keeping its aspect, centred on black."""
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
