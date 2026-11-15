import asyncio
import io
import logging
import wave
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import BinaryIO, NamedTuple

import av

from iris.llm.errors import UserFacingError
from iris.media.frames import FrameNormaliser, GridSampler, normalise_frame, rebuild_frames
from iris.models import (
    LIVE_WITHOUT_AUDIO,
    UNREADABLE_LIVE_AUDIO,
    FrameIndex,
    FrameRef,
)
from iris.pipeline.context import StageContext
from iris.store.layout import SessionPaths, read_audio_chunk_name, read_captured_frame_name

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 16 * 1024 * 1024

WEBM_MAGIC = b"\x1a\x45\xdf\xa3"
JPEG_MAGIC = b"\xff\xd8\xff"

AUDIO_GAP_S = 0.05
FRAME_WORKERS = 4


class ChunkConflict(Exception): ...


def store_audio_chunk(paths: SessionPaths, seq: int, data: bytes) -> bool:
    return _store_once(paths.live_audio_chunk(seq), data)


def store_frame(paths: SessionPaths, t: float, share: int, data: bytes) -> bool:
    return _store_once(paths.live_frame(t, share), data)


def _store_once(path: Path, data: bytes) -> bool:
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
    if not paths.live_audio_dir.is_dir():
        return []
    found = []
    for p in paths.live_audio_dir.iterdir():
        if (seq := read_audio_chunk_name(p.name)) is not None:
            found.append((seq, p))
    return sorted(found)


def missing_audio_chunks(paths: SessionPaths, expected: int) -> list[int]:
    have = {seq for seq, _ in audio_chunks(paths)}
    return [seq for seq in range(expected) if seq not in have]


class CapturedFrame(NamedTuple):
    t: float
    share: int
    path: Path


def captured_frames(paths: SessionPaths) -> list[CapturedFrame]:
    if not paths.live_frames_dir.is_dir():
        return []
    found = []
    for p in paths.live_frames_dir.iterdir():
        if (key := read_captured_frame_name(p.name)) is not None:
            found.append(CapturedFrame(*key, p))
    return sorted(found)


def split_shares(frames: Sequence[CapturedFrame]) -> list[list[CapturedFrame]]:
    shares: list[list[CapturedFrame]] = []
    for frame in frames:
        if not shares or shares[-1][-1].share != frame.share:
            shares.append([])
        shares[-1].append(frame)
    return shares


class BrowserLiveSource:
    async def ingest(self, ctx: StageContext) -> None:
        chunks = audio_chunks(ctx.paths)
        if not chunks:
            raise UserFacingError(LIVE_WITHOUT_AUDIO, "no audio was recorded")
        if chunks[0][0] != 0:
            raise UserFacingError(
                UNREADABLE_LIVE_AUDIO, "the first audio chunk (the stream header) is missing"
            )
        missing = missing_audio_chunks(ctx.paths, chunks[-1][0] + 1)
        if missing:
            log.warning(
                "session %s: audio chunks %s missing; filled with silence", ctx.session_id, missing
            )
        s = ctx.settings
        await asyncio.gather(
            asyncio.to_thread(finalize_audio, chunks, ctx.paths.audio, s.audio_sample_rate),
            asyncio.to_thread(finalize_frames, ctx.paths, s.frame_fps, s.frame_max_width),
        )


CLUSTER_ID = b"\x1f\x43\xb6\x75"
MAX_HEADER_BYTES = 1024 * 1024

Part = bytes | tuple[Path, int]


class ChunkReader(io.RawIOBase):
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
    runs: list[list[Path]] = []
    prev = None
    for seq, path in chunks:
        if prev is None or seq != prev + 1:
            runs.append([])
        runs[-1].append(path)
        prev = seq
    return runs


def find_cluster(files: Sequence[Path], limit: int | None = None) -> tuple[int, int] | None:
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
    runs = contiguous_runs(chunks)
    if not runs:
        raise UserFacingError(LIVE_WITHOUT_AUDIO, "no audio was recorded")
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with wave.open(str(tmp), "wb") as out:
            wav = PlacedWav(out, rate)
            only_run = len(runs) == 1
            _decode_run([(p, 0) for p in runs[0]], wav, required=only_run)
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
        raise UserFacingError(LIVE_WITHOUT_AUDIO, "the live recording contains no audio")
    tmp.replace(dest)
    return wav.samples / rate


class RawPcm:
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
        self.out.writeframes(frame.to_ndarray().tobytes())
        self.samples += frame.samples


def _decode_run(parts: Sequence[Part], wav: PlacedWav, *, required: bool) -> None:
    with ChunkReader(parts) as reader:
        try:
            container = av.open(reader)
        except av.FFmpegError as e:
            if required:
                raise UserFacingError(
                    UNREADABLE_LIVE_AUDIO, f"the live audio is not a readable recording: {e}"
                ) from e
            log.warning("skipping live audio that could not be opened: %s", e)
            return
        with container:
            if not container.streams.audio:
                raise UserFacingError(LIVE_WITHOUT_AUDIO, "the live recording has no audio track")
            for packet in container.demux(container.streams.audio[0]):
                try:
                    frames = packet.decode()
                except av.FFmpegError as e:
                    log.warning("skipping a damaged live audio packet: %s", e)
                    continue
                for frame in frames:
                    wav.write(frame)


def finalize_frames(paths: SessionPaths, fps: float, max_width: int) -> FrameIndex:
    return rebuild_frames(paths, lambda: _normalise_shares(paths, fps, max_width))


def _normalise_shares(paths: SessionPaths, fps: float, max_width: int) -> FrameIndex:
    normaliser = FrameNormaliser(paths.frames_dir, max_width)
    refs: list[FrameRef] = []
    starts: list[float] = []
    with ThreadPoolExecutor(FRAME_WORKERS) as pool:
        for share in split_shares(_sample_on_grid(captured_frames(paths), fps)):
            normaliser.new_share()
            share_refs = _normalise_share(normaliser, share, pool)
            if refs and share_refs:
                starts.append(share_refs[0].t)
            refs += share_refs
    width, height = normaliser.first_size or (0, 0)
    return FrameIndex(fps=fps, width=width, height=height, frames=refs, share_starts=starts)


def _normalise_share(
    normaliser: FrameNormaliser, share: Sequence[CapturedFrame], pool: ThreadPoolExecutor
) -> list[FrameRef]:
    for i, frame in enumerate(share):
        if (first := normaliser.add(frame.t, frame.path)) is not None:
            assert normaliser.size is not None
            rest = _normalise_in_size(normaliser.frames_dir, normaliser.size, share[i + 1 :], pool)
            return [first, *rest]
    return []


def _normalise_in_size(
    frames_dir: Path,
    size: tuple[int, int],
    frames: Sequence[CapturedFrame],
    pool: ThreadPoolExecutor,
) -> list[FrameRef]:
    sized = pool.map(lambda frame: normalise_frame(frames_dir, frame.t, frame.path, size), frames)
    return [ref for ref in sized if ref is not None]


def _sample_on_grid(frames: list[CapturedFrame], fps: float) -> list[CapturedFrame]:
    sampler = GridSampler(fps)
    return [frame for frame in frames if sampler.take(frame.t)]
