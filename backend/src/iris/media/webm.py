import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Literal, Protocol

import av

from iris.media.audio import SAMPLE_BYTES

CLUSTER_ID = b"\x1f\x43\xb6\x75"
MAX_HEADER_BYTES = 1024 * 1024
LONGEST_CODEC_JITTER_S = 0.05

NoChunkReason = Literal["gap", "end", "stop"]


class ChunkFeed(Protocol):
    """Hands a recording's WebM chunks to a ``ChunkStream`` by number, one after another.

    ``next_chunk(seq)`` returns chunk ``seq``'s file, or why there is none: ``"gap"`` when it is
    missing and a later chunk is there, ``"end"`` when no chunk at or after it will come, and
    ``"stop"`` when reading was cancelled. It may wait for the chunk to arrive.
    """

    def next_chunk(self, seq: int) -> Path | NoChunkReason: ...


class StoredChunks:
    def __init__(self, chunks: Sequence[tuple[int, Path]]):
        self._paths = dict(chunks)
        self._last = max(self._paths, default=-1)

    def next_chunk(self, seq: int) -> Path | NoChunkReason:
        if (path := self._paths.get(seq)) is not None:
            return path
        return "gap" if seq < self._last else "end"


class ChunkStream(io.RawIOBase):
    def __init__(self, feed: ChunkFeed, prefix: bytes, seq: int, path: Path, offset: int):
        self._feed = feed
        self._prefix = io.BytesIO(prefix)
        self.seq = seq
        self.ended: NoChunkReason | None = None
        self._file: io.BufferedReader | None = path.open("rb")
        self._file.seek(offset)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:  # type: ignore[override]
        if n := self._prefix.readinto(buffer):
            return n
        while self._file is not None:
            if n := self._file.readinto(buffer):
                return n
            got = self._feed.next_chunk(self.seq + 1)
            self._file.close()
            self._file = None
            if isinstance(got, Path):
                self.seq += 1
                self._file = got.open("rb")
            else:
                self.ended = got
        return 0

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        super().close()


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


class PlacedWav:
    def __init__(self, write_pcm: Callable[[bytes], object], rate: int):
        self._write = write_pcm
        self.rate = rate
        self.samples = 0
        self._gap = round(LONGEST_CODEC_JITTER_S * rate)
        self._resampler: av.AudioResampler | None = None

    def write(self, frame: av.AudioFrame) -> None:
        if frame.time is not None and round(frame.time * self.rate) - self.samples > self._gap:
            self.flush()
            lead = round(frame.time * self.rate) - self.samples
            if lead > 0:
                self._write(bytes(SAMPLE_BYTES * lead))
                self.samples += lead
        if self._resampler is None:
            self._resampler = av.AudioResampler(format="s16", layout="mono", rate=self.rate)
        for chunk in self._resampler.resample(frame):
            self._write_frame(chunk)

    def flush(self) -> None:
        if self._resampler is not None:
            for chunk in self._resampler.resample(None):
                self._write_frame(chunk)
            self._resampler = None

    def _write_frame(self, frame: av.AudioFrame) -> None:
        self._write(frame.to_ndarray().tobytes())
        self.samples += frame.samples
