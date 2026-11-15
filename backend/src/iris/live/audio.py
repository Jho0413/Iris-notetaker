import asyncio
import contextlib
import hashlib
import io
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import av
import numpy as np
from pydantic import BaseModel

from iris import speech
from iris.live.lane import LiveLane
from iris.live.timeline import Lane, LiveTimeline
from iris.llm import usage
from iris.llm.client import get_openai
from iris.llm.errors import describe, retry_temporary
from iris.models import EventType, Stage, Transcript, TranscriptSegment
from iris.pipeline.context import StageContext
from iris.pipeline.stages import transcribe
from iris.sources.browser_live import (
    PlacedWav,
    RawPcm,
    audio_chunks,
    find_cluster,
    stream_header,
)
from iris.store.layout import SessionPaths, read_model, write_model

log = logging.getLogger(__name__)

CONFIRM_S = 1.0
PROMPT_CHARS = 500
MIN_TAIL_S = 1.0
PROGRESS_S = 5.0


NoChunkReason = Literal["gap", "end", "stop"]


class LiveDecoder:
    def __init__(
        self, paths: SessionPaths, rate: int, gap_wait_s: float, notify: Callable[[], None]
    ):
        self.paths = paths
        self.rate = rate
        self.gap_wait_s = gap_wait_s
        self.samples = 0
        self.skipped = False
        self._notify = notify
        self._poke = threading.Event()
        self._ending = False
        self._cancelled = False
        self._file: io.BufferedWriter | None = None
        self._wav: PlacedWav | None = None

    def poke(self) -> None:
        self._poke.set()

    def end(self) -> None:
        self._ending = True
        self._poke.set()

    def cancel(self) -> None:
        self._cancelled = True
        self._poke.set()

    def run(self) -> None:
        self.paths.live_pcm.parent.mkdir(parents=True, exist_ok=True)
        with self.paths.live_pcm.open("wb") as f:
            self._file = f
            self._wav = PlacedWav(RawPcm(f), self.rate)
            self._decode_all()
            if not self._cancelled:
                self._wav.flush()
            self._report()

    def _decode_all(self) -> None:
        first = self.wait_chunk(0)
        if not isinstance(first, Path):
            if first == "gap":
                self._skip("the first audio chunk (the stream header) never arrived")
            return
        stream = ChunkStream(self, b"", 0, first, 0)
        self._decode(stream)
        run0 = [self.paths.live_audio_chunk(s) for s in range(stream.seq + 1)]
        header: bytes | None = None
        ended, lost = stream.ended, stream.seq + 1
        while ended == "gap":
            self._skip(f"audio chunk {lost} did not arrive in {self.gap_wait_s:g}s")
            if header is None:
                header = stream_header(run0)
                if header is None:
                    log.warning("%s: no stream header; later audio not decoded live", self.paths)
                    return
            found = self._find_cluster_after(lost)
            if found is None:
                return
            if isinstance(found, int):
                lost = found
                continue
            seq, offset = found
            stream = ChunkStream(self, header, seq, self.paths.live_audio_chunk(seq), offset)
            self._decode(stream)
            ended, lost = stream.ended, stream.seq + 1

    def _find_cluster_after(self, lost: int) -> tuple[int, int] | int | None:
        seq = self._next_stored(lost)
        if seq is None:
            return None
        files = [self.paths.live_audio_chunk(seq)]
        while True:
            found = find_cluster(files[-2:])
            if found is not None:
                index, offset = found
                return seq - (len(files[-2:]) - 1) + index, offset
            got = self.wait_chunk(seq + 1)
            if not isinstance(got, Path):
                return seq + 1 if got == "gap" else None
            seq += 1
            files.append(got)

    def _decode(self, stream: "ChunkStream") -> None:
        assert self._wav is not None
        with stream:
            try:
                container = av.open(stream)
            except av.FFmpegError as e:
                if not self._cancelled:
                    self._skip(f"live audio that could not be opened: {e}")
                return
            with container:
                if not container.streams.audio:
                    self._skip("the live recording has no audio track")
                    return
                reported = self._wav.samples
                try:
                    for packet in container.demux(container.streams.audio[0]):
                        if self._cancelled:
                            return
                        try:
                            frames = packet.decode()
                        except av.FFmpegError as e:
                            log.warning("skipping a damaged live audio packet: %s", e)
                            continue
                        for frame in frames:
                            self._wav.write(frame)
                        if self._wav.samples - reported >= PROGRESS_S * self.rate:
                            reported = self._wav.samples
                            self._report()
                except av.FFmpegError as e:
                    if not self._cancelled:
                        self._skip(f"live audio stream could not be read on: {e}")

    def wait_chunk(self, seq: int) -> Path | NoChunkReason:
        gap_since: float | None = None
        while True:
            self._forget_wakeups_before_checking()
            if self._cancelled:
                return "stop"
            path = self.paths.live_audio_chunk(seq)
            if path.exists():
                return path
            later = self._next_stored(seq) is not None
            if self._ending:
                return "gap" if later else "end"
            timeout = None
            if later and seq > 0:
                if gap_since is None:
                    gap_since = time.monotonic()
                timeout = self.gap_wait_s - (time.monotonic() - gap_since)
                if timeout <= 0:
                    return "gap"
            self._report()
            self._poke.wait(timeout)

    def _forget_wakeups_before_checking(self) -> None:
        self._poke.clear()

    def _next_stored(self, seq: int) -> int | None:
        return next((s for s, _ in audio_chunks(self.paths) if s > seq), None)

    def _skip(self, reason: str) -> None:
        log.warning("%s: %s; skipped live", self.paths.root.name, reason)
        self.skipped = True
        self._notify()

    def _report(self) -> None:
        if self._file is None or self._wav is None or self._wav.samples == self.samples:
            return
        self._file.flush()
        self.samples = self._wav.samples
        self._notify()


class ChunkStream(io.RawIOBase):
    def __init__(
        self,
        decoder: LiveDecoder,
        prefix: bytes,
        seq: int,
        path: Path,
        offset: int,
    ):
        self._decoder = decoder
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
            got = self._decoder.wait_chunk(self.seq + 1)
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


def window_cut(samples: np.ndarray, rate: int, min_len: int, max_len: int) -> int:
    cut = transcribe.quiet_profile(samples, rate).quietest(min_len, max_len, target=max_len)
    return max_len if cut is None else cut


class CachedWindow(BaseModel):
    index: int
    start: float
    end: float
    audio_sha1: str
    model: str
    requested_language: str | None = None
    prompt: str
    prompt_through: int
    language: str | None = None
    segments: list[TranscriptSegment]


class WindowFailed(RuntimeError):
    pass


def _within(segments: list[TranscriptSegment], start: float, end: float) -> list[TranscriptSegment]:
    clamped = []
    for seg in segments:
        a = min(max(seg.start, start), end)
        b = min(max(seg.end, a), end)
        if (a, b) != (seg.start, seg.end):
            seg = seg.model_copy(update={"start": a, "end": b})
        clamped.append(seg)
    return sorted(clamped, key=lambda seg: (seg.start, seg.end))


def _load_window(path: Path) -> CachedWindow | None:
    if not path.exists():
        return None
    try:
        return read_model(path, CachedWindow)
    except ValueError:
        log.warning("ignoring unreadable transcript window %s", path)
        return None


class AudioLane(LiveLane):
    lane = Lane.AUDIO

    def __init__(self, ctx: StageContext, timeline: LiveTimeline):
        super().__init__(ctx, timeline)
        s = ctx.settings
        self.rate = s.audio_sample_rate
        self.model = s.transcribe_model
        self.language = s.transcribe_language
        self.min_len = max(1, int(s.live_window_min_s * self.rate))
        self.max_len = int(s.live_window_max_s * self.rate)
        self.confirm = int(CONFIRM_S * self.rate)
        self.min_tail = int(MIN_TAIL_S * self.rate)
        self.concurrency = s.live_transcribe_concurrency
        self._changed = asyncio.Event()
        self._commits = asyncio.Condition()
        self._committed = 0
        self._segment_counts: list[int] = []
        self._kept: set[str] = set()
        self._client = None

    async def run(self) -> None:
        longest = self.max_len + max(self.confirm, self.min_tail)
        if longest * 2 + 1024 > transcribe.API_MAX_UPLOAD_BYTES:
            raise ValueError("live_window_max_s exceeds the transcription API's upload limit")
        paths = self.ctx.paths
        paths.live_transcript_windows_dir.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()

        def notify() -> None:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._changed.set)

        decoder = LiveDecoder(paths, self.rate, self.ctx.settings.live_audio_gap_wait_s, notify)
        decoding = asyncio.create_task(asyncio.to_thread(decoder.run))
        decoding.add_done_callback(lambda _: self._changed.set())
        forwarding = asyncio.create_task(self._forward_input(decoder))
        windows: dict[int, asyncio.Task[CachedWindow]] = {}
        try:
            await self._process(decoder, decoding, windows)
        finally:
            decoder.cancel()
            forwarding.cancel()
            for task in windows.values():
                task.cancel()
            await asyncio.gather(decoding, forwarding, *windows.values(), return_exceptions=True)

    async def _forward_input(self, decoder: LiveDecoder) -> None:
        while True:
            await self.wait_for_input()
            if self.timeline.ending:
                decoder.end()
                self._changed.set()
                return
            decoder.poke()

    async def _process(
        self,
        decoder: LiveDecoder,
        decoding: asyncio.Task[None],
        windows: dict[int, asyncio.Task[CachedWindow]],
    ) -> None:
        cut_at = 0
        planned: deque[tuple[int, int]] = deque()
        next_index = 0
        finished: dict[int, CachedWindow] = {}
        while True:
            self._changed.clear()
            if decoding.done() and (exc := decoding.exception()) is not None:
                raise exc
            if decoder.skipped and Lane.AUDIO not in self.timeline.skipped_input:
                await self.timeline.mark_skipped_input(Lane.AUDIO)
            final = self.timeline.ending and decoding.done()

            while (cut := await self._next_cut(cut_at, decoder.samples, final)) is not None:
                planned.append((cut_at, cut))
                cut_at = cut
            while planned and next_index < self._committed + self.concurrency:
                start, end = planned.popleft()
                task = asyncio.create_task(self._window(next_index, start, end))
                task.add_done_callback(lambda _: self._changed.set())
                windows[next_index] = task
                next_index += 1
            for i in [i for i, task in windows.items() if task.done()]:
                finished[i] = windows.pop(i).result()
            while self._committed in finished:
                await self._commit(finished.pop(self._committed))

            if final and cut_at >= decoder.samples and not (planned or windows or finished):
                break
            await self._changed.wait()
        await self._finish(decoder.samples)

    async def _next_cut(self, cut_at: int, decoded: int, final: bool) -> int | None:
        remaining = decoded - cut_at
        if remaining <= 0:
            return None
        if remaining > self.max_len and (final or remaining >= self.max_len + self.confirm):
            end = min(decoded, cut_at + self.max_len + self.confirm)
            samples = await asyncio.to_thread(self._read, cut_at, end)
            rel = await asyncio.to_thread(
                window_cut, samples, self.rate, self.min_len, self.max_len
            )
            cut = cut_at + rel
            if final and decoded - cut < self.min_tail:
                return decoded
            return cut
        return decoded if final else None

    def _read(self, start: int, end: int) -> np.ndarray:
        samples = np.fromfile(
            self.ctx.paths.live_pcm, dtype="<i2", count=end - start, offset=2 * start
        )
        if len(samples) != end - start:
            raise RuntimeError(f"live/audio.pcm is shorter than the {end} samples decoded")
        return samples

    def _cache_path(self, i: int, start: int, end: int) -> Path:
        ms = f"{round(start * 1000 / self.rate)}_{round(end * 1000 / self.rate)}"
        return self.ctx.paths.live_transcript_windows_dir / f"{i}_{ms}.json"

    def _reusable(self, cached: CachedWindow, i: int, start: int, end: int, digest: str) -> bool:
        t0, t1 = start / self.rate, end / self.rate
        return (
            cached.index == i
            and cached.audio_sha1 == digest
            and cached.model == self.model
            and cached.requested_language == self.language
            and abs(cached.start - t0) < 1e-6
            and abs(cached.end - t1) < 1e-6
            and -1 <= cached.prompt_through < i
            and all(t0 - 1e-6 <= s.start <= s.end <= t1 + 1e-6 for s in cached.segments)
        )

    async def _window(self, i: int, start: int, end: int) -> CachedWindow:
        samples = await asyncio.to_thread(self._read, start, end)
        digest = hashlib.sha1(samples).hexdigest()
        path = self._cache_path(i, start, end)
        cached = await asyncio.to_thread(_load_window, path)
        if cached is not None and self._reusable(cached, i, start, end, digest):
            await self._wait_committed(cached.prompt_through + 1)
            if cached.prompt == self._prompt(cached.prompt_through):
                return cached

        through = self._committed - 1
        prompt = self._prompt(through)
        language, segments = None, []
        for a, b in await asyncio.to_thread(speech.find_speech, samples, self.rate):
            at = (start + a) / self.rate
            result = await self._transcribe(i, samples[a:b], at, prompt)
            language = language or result.language
            segments += [
                seg.model_copy(update={"start": seg.start + at, "end": seg.end + at})
                for seg in result.segments
            ]
        segments = _within(segments, start / self.rate, end / self.rate)
        window = CachedWindow(
            index=i,
            start=start / self.rate,
            end=end / self.rate,
            audio_sha1=digest,
            model=self.model,
            requested_language=self.language,
            prompt=prompt,
            prompt_through=through,
            language=language,
            segments=segments,
        )
        await asyncio.to_thread(write_model, path, window)
        return window

    async def _transcribe(
        self, i: int, samples: np.ndarray, offset: float, prompt: str
    ) -> transcribe.ChunkTranscript:
        if self._client is None:
            self._client = get_openai(self.ctx.settings)
        wav = transcribe.encode_wav(samples, self.rate)
        where = f"session {self.ctx.session_id}: live window {i} at {offset:.1f}s"
        client = self._client

        async def call() -> transcribe.ChunkTranscript:
            with usage.recording(self.ctx, Stage.TRANSCRIBE):
                return await transcribe.transcribe_chunk(
                    client,
                    self.model,
                    wav,
                    len(samples) / self.rate,
                    prompt or None,
                    language=self.language,
                )

        try:
            return await retry_temporary(call, self.ctx.settings.api_retry_delays_s, what=where)
        except Exception as exc:
            raise WindowFailed(f"{where} failed: {describe(exc)}") from exc

    async def _wait_committed(self, n: int) -> None:
        async with self._commits:
            await self._commits.wait_for(lambda: self._committed >= n)

    def _prompt(self, through: int) -> str:
        if through < 0 or not transcribe.supports_prompt(self.model):
            return ""
        before = self._segment_counts[through - 1] if through > 0 else 0
        if self._segment_counts[through] == before:
            return ""
        segments = self.timeline.segments
        parts: list[str] = []
        size = 0
        for k in range(self._segment_counts[through] - 1, -1, -1):
            parts.append(segments[k].text)
            size += len(segments[k].text) + 1
            if size > PROMPT_CHARS:
                break
        text = " ".join(reversed(parts))
        if len(text) > PROMPT_CHARS:
            text = text[-PROMPT_CHARS:]
            if " " in text:
                text = text.split(" ", 1)[1]
        return text

    async def _commit(self, window: CachedWindow) -> None:
        segments = _within(window.segments, window.start, window.end)
        language = window.language if segments else None
        transcript = Transcript(
            language=self.timeline.language or language,
            segments=[*self.timeline.segments, *segments],
        )
        await asyncio.to_thread(write_model, self.ctx.paths.live_transcript, transcript)
        self.ctx.emit(
            EventType.TRANSCRIPT_CHUNK,
            {"chunk": window.index, "segments": [s.model_dump() for s in segments]},
        )
        await self.timeline.publish_transcript(segments, window.end, language)
        self._committed += 1
        self._segment_counts.append(len(self.timeline.segments))
        start, end = round(window.start * self.rate), round(window.end * self.rate)
        self._kept.add(self._cache_path(window.index, start, end).name)
        async with self._commits:
            self._commits.notify_all()
        self._changed.set()

    async def _finish(self, decoded: int) -> None:
        await self.timeline.publish_transcript([], decoded / self.rate)
        if not self._committed:
            empty = Transcript(segments=[])
            await asyncio.to_thread(write_model, self.ctx.paths.live_transcript, empty)
        for old in self.ctx.paths.live_transcript_windows_dir.glob("*.json"):
            if old.name not in self._kept:
                old.unlink(missing_ok=True)
