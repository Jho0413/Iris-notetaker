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

from iris.aio import cancel_and_wait
from iris.media.container import first_video_stream
from iris.media.images import shrink_to_width

OPUS_RATE = 48_000
OPUS_FRAME = 960
CHUNK_S = 5.0
FRAME_INTERVAL_S = 1.0
FRAME_MAX_WIDTH = 1920
JPEG_QUALITY = 80
MUX_BUFFER_BYTES = 4096


@dataclass
class EncodedWebm:
    data: bytes
    marks: list[tuple[float, int]]

    @property
    def duration_s(self) -> float:
        return self.marks[-1][0] if self.marks else 0.0


def encode_webm(
    blocks: Iterable[np.ndarray], rate: int = OPUS_RATE, cluster_ms: int = 500
) -> EncodedWebm:
    buf = io.BytesIO()
    marks: list[tuple[float, int]] = []
    pending = np.zeros(0, np.float32)
    k = 0
    with av.open(
        buf,
        "w",
        format="webm",
        options={"cluster_time_limit": str(cluster_ms)},
        buffer_size=MUX_BUFFER_BYTES,
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
    marks.append((k * OPUS_FRAME / rate, len(data)))
    return EncodedWebm(data, marks)


def chunk_webm(webm: EncodedWebm, chunk_s: float = CHUNK_S) -> list[tuple[float, bytes]]:
    chunks: list[tuple[float, bytes]] = []
    marks, end_t = webm.marks, webm.duration_s
    start = upto = j = 0
    boundary = chunk_s
    while boundary < end_t:
        while j < len(marks) and marks[j][0] <= boundary + 1e-9:
            upto = max(upto, marks[j][1])
            j += 1
        if upto > start:
            chunks.append((boundary, webm.data[start:upto]))
            start = upto
        boundary += chunk_s
    if start < len(webm.data):
        chunks.append((end_t, webm.data[start:]))
    return chunks


def file_audio(path: Path, rate: int = OPUS_RATE) -> Iterator[np.ndarray]:
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
                    if at > written:
                        yield np.zeros(at - written, np.float32)
                        written = at
                yield samples
                written += len(samples)
        for out in resampler.resample(None):
            yield out.to_ndarray().reshape(-1)


def encode_jpeg(image: np.ndarray, max_width: int = FRAME_MAX_WIDTH) -> bytes:
    image = shrink_to_width(image, max_width)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise ValueError("could not encode a frame")
    return buf.tobytes()


def file_frames(
    path: Path, interval_s: float = FRAME_INTERVAL_S, until: float | None = None
) -> Iterator[tuple[float, bytes]]:
    with av.open(str(path)) as container:
        stream = first_video_stream(container)
        if stream is None:
            return
        stream.thread_type = "AUTO"
        due, last_t = 0.0, 0.0
        latest: bytes | None = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            t = float(frame.pts * stream.time_base)
            if t < 0:
                continue
            while latest is not None and due + 1e-6 < t and (until is None or due <= until):
                yield due, latest
                due += interval_s
            if until is not None and t > until:
                break
            if latest is None:
                due = math.ceil(t / interval_s - 1e-6) * interval_s
            latest, last_t = encode_jpeg(frame.to_ndarray(format="bgr24")), t
        end = last_t if until is None else until
        while latest is not None and due <= end + 1e-6:
            yield due, latest
            due += interval_s


@dataclass
class ReplayReport:
    session_id: str
    duration_s: float
    audio_chunks: int
    frames: int
    status: str = "unknown"
    error: str | None = None
    first: dict[str, float] = field(default_factory=dict)
    sections_live: int = 0
    promoted: list[str] = field(default_factory=list)
    notes_to_done_s: float | None = None
    cost_usd: float | None = None
    cost_after_notes_usd: float | None = None
    calls: int = 0
    calls_after_notes: dict[str, int] = field(default_factory=dict)

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
            f"  promoted at make notes: {', '.join(self.promoted) or 'nothing'}",
        ]
        if self.notes_to_done_s is not None:
            out.append(f"  make notes to done: {self.notes_to_done_s:.1f} s")
        if self.cost_usd is not None:
            after = ", ".join(f"{s} {n}" for s, n in sorted(self.calls_after_notes.items()))
            out.append(
                f"  cost ${self.cost_usd:.4f} over {self.calls} calls; after make notes "
                f"${self.cost_after_notes_usd or 0:.4f} ({after or 'no calls'})"
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
    try:
        import httpx
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

        def session_seconds() -> float:
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
            with ThreadPoolExecutor(1) as frame_decoder_thread:
                frames = file_frames(file, until=webm.duration_s)
                while (
                    item := await loop.run_in_executor(frame_decoder_thread, next, frames, None)
                ) is not None:
                    t, jpeg = item
                    await until(t)
                    await post(f"frame?t={t:.3f}&share=1", jpeg)
                    report.frames += 1

        done = asyncio.Event()
        notes_at: list[float] = []

        async def follow() -> None:
            async with client.stream("GET", f"{api}/sessions/{sid}/events", timeout=None) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    kind, data = event["type"], event.get("data") or {}
                    report.first.setdefault(kind, session_seconds())
                    if kind == "notes_updated" and data.get("live") and not notes_at:
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
            notes_wall = datetime.now(UTC)
            notes_at.append(loop.time())
            log(f"  make notes at {session_seconds():.1f} s")
            r = await client.post(f"{api}/live/{sid}/end", json={"audio_chunks": len(chunks)})
            r.raise_for_status()
            await asyncio.wait_for(done.wait(), timeout_s)
            report.notes_to_done_s = loop.time() - notes_at[0]
        finally:
            await cancel_and_wait([follower])

        session = (await client.get(f"{api}/sessions/{sid}")).json()
        report.error = session.get("failure_message")
        usage = (await client.get(f"{api}/sessions/{sid}/usage")).json()
        records = usage.get("records", [])
        report.cost_usd = usage.get("total", {}).get("cost_usd")
        report.calls = len(records)
        after = [r for r in records if datetime.fromisoformat(r["at"]) >= notes_wall]
        report.cost_after_notes_usd = sum(r.get("cost_usd") or 0 for r in after)
        for r in after:
            stage = r.get("stage") or "none"
            report.calls_after_notes[stage] = report.calls_after_notes.get(stage, 0) + 1
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
