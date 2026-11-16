import asyncio
import logging
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import NamedTuple

import av

from iris.llm.errors import UserFacingError
from iris.media.audio import open_mono_wav
from iris.media.frames import FrameNormaliser, GridSampler, normalise_frame, rebuild_frames
from iris.media.webm import ChunkStream, PlacedWav, StoredChunks, find_cluster, stream_header
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


def contiguous_runs(chunks: Sequence[tuple[int, Path]]) -> list[list[tuple[int, Path]]]:
    runs: list[list[tuple[int, Path]]] = []
    prev = None
    for seq, path in chunks:
        if prev is None or seq != prev + 1:
            runs.append([])
        runs[-1].append((seq, path))
        prev = seq
    return runs


def _files(run: Sequence[tuple[int, Path]]) -> list[Path]:
    return [path for _, path in run]


def finalize_audio(chunks: Sequence[tuple[int, Path]], dest: Path, rate: int) -> float:
    runs = contiguous_runs(chunks)
    if not runs:
        raise UserFacingError(LIVE_WITHOUT_AUDIO, "no audio was recorded")
    feed = StoredChunks(chunks)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with open_mono_wav(str(tmp), rate) as out:
            wav = PlacedWav(out.writeframes, rate)
            first_seq, first_path = runs[0][0]
            _decode_run(
                ChunkStream(feed, b"", first_seq, first_path, 0), wav, required=len(runs) == 1
            )
            header = stream_header(_files(runs[0])) if len(runs) > 1 else None
            for run in runs[1:]:
                found = find_cluster(_files(run))
                if header is None or found is None:
                    log.warning("no decodable cluster after a gap; %d chunks skipped", len(run))
                    continue
                index, offset = found
                seq, path = run[index]
                _decode_run(ChunkStream(feed, header, seq, path, offset), wav, required=False)
            wav.flush()
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if wav.samples == 0:
        tmp.unlink(missing_ok=True)
        raise UserFacingError(LIVE_WITHOUT_AUDIO, "the live recording contains no audio")
    tmp.replace(dest)
    return wav.samples / rate


def _decode_run(stream: ChunkStream, wav: PlacedWav, *, required: bool) -> None:
    with stream:
        try:
            container = av.open(stream)
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
            write = partial(normalise_frame, normaliser.frames_dir, size=normaliser.size)
            rest = share[i + 1 :]
            sized = pool.map(write, [f.t for f in rest], [f.path for f in rest])
            return [first, *(ref for ref in sized if ref is not None)]
    return []


def _sample_on_grid(frames: list[CapturedFrame], fps: float) -> list[CapturedFrame]:
    sampler = GridSampler(fps)
    return [frame for frame in frames if sampler.take(frame.t)]
