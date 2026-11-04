"""Replay a recording into a running backend's live API, as the browser would (a dev tool).

``iris replay-live <file>`` (``cli.py``) plays a recorded file into ``/api/live``:

- **Audio** is re-encoded with PyAV into one WebM/Opus stream (48 kHz mono, short clusters, as
  MediaRecorder writes it) and posted as consecutive byte ranges of about ``CHUNK_S`` of audio
  each, with ``seq`` from 0. Only the first carries the header, as with MediaRecorder timeslices.
- **Screen frames** are sampled about once a second (at each second, the latest video frame shown
  by then, as the browser's frame ticker captures it), scaled to at most ``FRAME_MAX_WIDTH`` wide
  like the browser does, and posted as JPEG with ``t`` (seconds since start, the audio's clock).
- Everything is **paced in real time** (``speed`` > 1 plays faster: the session clock stays the
  file's), then ``POST /end`` is sent with the chunk count.

It follows the session's events and reports when the first transcript, moment, figure and notes
section arrived, how many notes sections were written before stop, how long stop took to reach
``done``, whether stop promoted the live work, and the model cost (total and after stop) from
``/api/sessions/{id}/usage``.

The encoding helpers (``encode_webm``, ``chunk_webm``) are shared with the tests, which build
synthetic recordings with them. PyAV only: no ``ffmpeg`` CLI.
"""

import asyncio
import io
import json
import math
import time
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

import av
import cv2
import numpy as np

OPUS_RATE = 48_000
OPUS_FRAME = 960  # 20 ms
CHUNK_S = 5.0  # the browser's MediaRecorder timeslice
FRAME_INTERVAL_S = 1.0  # the browser's frame ticker
FRAME_MAX_WIDTH = 1920  # the browser scales frames to at most this width
JPEG_QUALITY = 80


# --- Encoding (shared with the tests) ---------------------------------------------------------


@dataclass
class EncodedWebm:
    data: bytes
    # (t, n): the first n bytes hold the stream up to t seconds of audio, in increasing order.
    marks: list[tuple[float, int]]

    @property
    def duration_s(self) -> float:
        return self.marks[-1][0] if self.marks else 0.0


def encode_webm(
    blocks: Iterable[np.ndarray], rate: int = OPUS_RATE, cluster_ms: int = 500
) -> EncodedWebm:
    """Encode mono float32 samples (in blocks of any length) as one WebM/Opus stream.

    Clusters are short, as in MediaRecorder output (FFmpeg's default is a few seconds), so a
    stream cut into byte ranges can be resumed at a cluster near any cut.
    """
    buf = io.BytesIO()
    marks: list[tuple[float, int]] = []
    pending = np.zeros(0, np.float32)
    k = 0
    # A small I/O buffer, so the bytes written track the audio muxed (for ``marks``).
    with av.open(
        buf, "w", format="webm", options={"cluster_time_limit": str(cluster_ms)}, buffer_size=4096
    ) as out:
        stream = out.add_stream("libopus", rate=rate)
        stream.layout = "mono"

        def encode(samples: np.ndarray | None) -> None:
            nonlocal k
            if samples is None:
                packets = stream.encode()
            else:
                frame = av.AudioFrame.from_ndarray(
                    samples.reshape(1, -1), format="flt", layout="mono"
                )
                frame.sample_rate = rate
                frame.pts = k * OPUS_FRAME
                frame.time_base = Fraction(1, rate)
                packets = stream.encode(frame)
                k += 1
            out.mux(packets)
            marks.append((k * OPUS_FRAME / rate, buf.tell()))

        for block in blocks:
            pending = np.concatenate([pending, np.asarray(block, np.float32).ravel()])
            whole = len(pending) // OPUS_FRAME * OPUS_FRAME
            for i in range(0, whole, OPUS_FRAME):
                encode(pending[i : i + OPUS_FRAME])
            pending = pending[whole:]
        if len(pending):
            encode(np.pad(pending, (0, OPUS_FRAME - len(pending))))
        encode(None)
    data = buf.getvalue()
    marks.append((k * OPUS_FRAME / rate, len(data)))  # the trailer
    return EncodedWebm(data, marks)


def chunk_webm(webm: EncodedWebm, chunk_s: float = CHUNK_S) -> list[tuple[float, bytes]]:
    """Consecutive byte ranges holding about ``chunk_s`` of audio each, as ``(t, bytes)``: a range
    can be posted once ``t`` seconds have been recorded. Joined, they are the whole stream."""
    chunks: list[tuple[float, bytes]] = []
    marks, end_t = webm.marks, webm.duration_s
    start = upto = j = 0
    boundary = chunk_s
    while boundary < end_t:
        while j < len(marks) and marks[j][0] <= boundary + 1e-9:
            upto = max(upto, marks[j][1])
            j += 1
        if upto > start:  # nothing written yet: this range joins the next
            chunks.append((boundary, webm.data[start:upto]))
            start = upto
        boundary += chunk_s
    if start < len(webm.data):
        chunks.append((end_t, webm.data[start:]))
    return chunks


def file_audio(path: Path, rate: int = OPUS_RATE) -> Iterator[np.ndarray]:
    """The file's first audio stream as mono float32 blocks at ``rate``, placed by timestamp
    from 0 (leading silence kept)."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=rate)
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError(f"{path.name} has no audio")
        stream = container.streams.audio[0]
        written = 0
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                samples = out.to_ndarray().reshape(-1)
                if out.pts is not None and out.time_base is not None:
                    at = round(float(out.pts * out.time_base) * rate)
                    if at > written:  # a gap (or a late start): silence
                        yield np.zeros(at - written, np.float32)
                        written = at
                yield samples
                written += len(samples)
        for out in resampler.resample(None):
            yield out.to_ndarray().reshape(-1)


def encode_jpeg(image: np.ndarray, max_width: int = FRAME_MAX_WIDTH) -> bytes:
    height, width = image.shape[:2]
    if width > max_width:
        size = (max_width, round(height * max_width / width))
        image = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("could not encode a frame")
    return buf.tobytes()


def file_frames(
    path: Path, interval_s: float = FRAME_INTERVAL_S, until: float | None = None
) -> Iterator[tuple[float, bytes]]:
    """What the browser's frame ticker captures: at each ``k * interval_s`` (up to ``until``,
    else the last frame), the latest video frame shown by then, as ``(t, JPEG)``.

    Screen recordings often store a frame only when the screen changes; the browser still sends
    one every tick, so a frame is repeated until the next one is shown. Ticks start at the first
    frame shown: nothing is sent for a time before the video has a picture.
    """
    with av.open(str(path)) as container:
        if not container.streams.video:
            return
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        due, last_t = 0.0, 0.0
        latest: bytes | None = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)  # the audio's clock (``file_audio``)
            if t < 0:
                continue
            while latest is not None and due + 1e-6 < t and (until is None or due <= until):
                yield due, latest
                due += interval_s
            if until is not None and t > until:
                break
            if latest is None:
                due = math.ceil(t / interval_s - 1e-6) * interval_s  # the first tick showing it
            latest, last_t = encode_jpeg(frame.to_ndarray(format="bgr24")), t
        end = last_t if until is None else until
        while latest is not None and due <= end + 1e-6:
            yield due, latest
            due += interval_s


# --- Replay -----------------------------------------------------------------------------------


@dataclass
class ReplayReport:
    session_id: str
    duration_s: float
    audio_chunks: int
    frames: int
    status: str = "unknown"
    error: str | None = None
    # Seconds after recording started that each event type first arrived (wall clock, scaled
    # back to session time when sped up).
    first: dict[str, float] = field(default_factory=dict)
    sections_live: int = 0  # notes sections written before stop (live notes_updated)
    promoted: list[str] = field(default_factory=list)
    stop_to_done_s: float | None = None
    cost_usd: float | None = None
    cost_after_stop_usd: float | None = None
    calls: int = 0
    calls_after_stop: dict[str, int] = field(default_factory=dict)  # by stage

    def lines(self) -> list[str]:
        def at(kind: str) -> str:
            t = self.first.get(kind)
            return "never" if t is None else f"{t:.1f} s"

        out = [
            f"session {self.session_id}: {self.status}"
            + (f" ({self.error})" if self.error else ""),
            f"  replayed {self.duration_s:.1f} s: {self.audio_chunks} audio chunks, "
            f"{self.frames} frames",
            f"  first transcript {at('transcript_chunk')}, moment {at('moment_found')}, "
            f"figure {at('visual_selected')}, notes section {at('notes_updated')}",
            f"  notes sections written live: {self.sections_live}",
            f"  promoted at stop: {', '.join(self.promoted) or 'nothing'}",
        ]
        if self.stop_to_done_s is not None:
            out.append(f"  stop to done: {self.stop_to_done_s:.1f} s")
        if self.cost_usd is not None:
            after = ", ".join(f"{s} {n}" for s, n in sorted(self.calls_after_stop.items()))
            out.append(
                f"  cost ${self.cost_usd:.4f} over {self.calls} calls; after stop "
                f"${self.cost_after_stop_usd or 0:.4f} ({after or 'no calls'})"
            )
        return out


async def replay(
    base_url: str,
    file: Path,
    speed: float = 1.0,
    title: str | None = None,
    timeout_s: float = 1800.0,
    log=print,
) -> ReplayReport:
    """Play ``file`` into the live API at ``base_url`` and follow it until done or failed."""
    try:
        import httpx  # a dev dependency: this is a dev tool, and the encoding helpers don't need it
    except ImportError as exc:
        raise RuntimeError(
            "iris replay-live needs the dev dependencies: run it with `uv run`"
        ) from exc

    if speed <= 0:
        raise ValueError("speed must be positive")
    webm = await asyncio.to_thread(encode_webm, file_audio(file))
    chunks = chunk_webm(webm)
    api = base_url.rstrip("/") + "/api"
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{api}/live", json={"title": title or file.stem})
        r.raise_for_status()
        sid = r.json()["id"]
        log(f"session {sid}: replaying {file.name} ({webm.duration_s:.1f} s) at {speed:g}x")
        report = ReplayReport(sid, webm.duration_s, len(chunks), 0)
        loop = asyncio.get_running_loop()
        t0 = loop.time()

        def now() -> float:  # session seconds since recording started
            return (loop.time() - t0) * speed

        async def until(t: float) -> None:
            await asyncio.sleep(max(0.0, t / speed - (loop.time() - t0)))

        async def post(path: str, data: bytes) -> None:
            for attempt in range(5):
                try:
                    r = await client.post(f"{api}/live/{sid}/{path}", content=data)
                    r.raise_for_status()
                    return
                except httpx.HTTPError as exc:
                    if attempt == 4:
                        raise
                    log(f"  retrying {path}: {exc}")
                    await asyncio.sleep(1 + attempt)

        async def send_audio() -> None:
            for seq, (t, data) in enumerate(chunks):
                await until(t)
                await post(f"audio?seq={seq}", data)

        async def send_frames() -> None:
            with ThreadPoolExecutor(1) as pool:  # one thread: the decoder is not shared
                frames = file_frames(file, until=webm.duration_s)  # stops with the audio
                while (item := await loop.run_in_executor(pool, next, frames, None)) is not None:
                    t, jpeg = item
                    await until(t)
                    await post(f"frame?t={t:.3f}", jpeg)
                    report.frames += 1

        done = asyncio.Event()
        stopped_at: list[float] = []

        async def follow() -> None:
            async with client.stream("GET", f"{api}/sessions/{sid}/events", timeout=None) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    kind, data = event["type"], event.get("data") or {}
                    report.first.setdefault(kind, now())
                    if kind == "notes_updated" and data.get("live") and not stopped_at:
                        report.sections_live = data.get("completed_parts", report.sections_live)
                    if kind == "stage_finished" and data.get("promoted"):
                        report.promoted.append(data["stage"])
                    if kind == "status" and data.get("status") in {"done", "failed"}:
                        report.status = data["status"]
                        done.set()
                        return

        follower = asyncio.create_task(follow())
        try:
            await asyncio.gather(send_audio(), send_frames())
            await until(webm.duration_s)
            stop_wall = datetime.now(UTC)
            stopped_at.append(loop.time())
            log(f"  stop at {now():.1f} s")
            r = await client.post(f"{api}/live/{sid}/end", json={"audio_chunks": len(chunks)})
            r.raise_for_status()
            await asyncio.wait_for(done.wait(), timeout_s)
            report.stop_to_done_s = loop.time() - stopped_at[0]
        finally:
            follower.cancel()
            await asyncio.gather(follower, return_exceptions=True)

        session = (await client.get(f"{api}/sessions/{sid}")).json()
        # Only our own message: the raw error is in the backend log and `iris show <id>`.
        report.error = session.get("failure_message")
        usage = (await client.get(f"{api}/sessions/{sid}/usage")).json()
        records = usage.get("records", [])
        report.cost_usd = usage.get("total", {}).get("cost_usd")
        report.calls = len(records)
        after = [r for r in records if datetime.fromisoformat(r["at"]) >= stop_wall]
        report.cost_after_stop_usd = sum(r.get("cost_usd") or 0 for r in after)
        for r in after:
            stage = r.get("stage") or "none"
            report.calls_after_stop[stage] = report.calls_after_stop.get(stage, 0) + 1
    return report


def main(base_url: str, files: list[Path], speed: float) -> int:
    failed = False
    for file in files:
        started = time.monotonic()
        report = asyncio.run(replay(base_url, file, speed))
        for line in report.lines():
            print(line)
        print(f"  wall time {time.monotonic() - started:.1f} s")
        failed = failed or report.status != "done"
    return 1 if failed else 0
