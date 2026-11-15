import asyncio
import shutil
import wave
from pathlib import Path
from typing import BinaryIO

import av
from av.container import InputContainer

from iris.llm.errors import UserFacingError
from iris.media.audio import SAMPLE_BYTES, open_mono_wav
from iris.media.container import first_video_stream, session_origin
from iris.media.frames import GridSampler, output_size, rebuild_frames, to_upright_bgr, write_frame
from iris.models import UNREADABLE_UPLOAD, UPLOAD_WITHOUT_AUDIO, FrameIndex, FrameRef
from iris.pipeline.context import StageContext
from iris.store.layout import SessionPaths


def save_upload(paths: SessionPaths, filename: str, data: BinaryIO) -> Path:
    ext = Path(filename).suffix.lower() or ".bin"
    dest = paths.source(ext)
    with dest.open("wb") as out:
        shutil.copyfileobj(data, out)
    return dest


class UploadSource:
    async def ingest(self, ctx: StageContext) -> None:
        src = ctx.paths.find_source()
        if src is None:
            raise FileNotFoundError("no source.* file in session")
        check_has_audio(src)
        s = ctx.settings
        try:
            await asyncio.gather(
                asyncio.to_thread(extract_audio, src, ctx.paths.audio, s.audio_sample_rate),
                asyncio.to_thread(ingest_frames, src, ctx.paths, s.frame_fps, s.frame_max_width),
            )
        except av.FFmpegError as e:
            raise UserFacingError(UNREADABLE_UPLOAD, f"{src.name} could not be decoded: {e}") from e


def open_media(src: Path) -> InputContainer:
    try:
        return av.open(str(src))
    except av.FFmpegError as e:
        raise UserFacingError(
            UNREADABLE_UPLOAD, f"{src.name} is not a readable audio or video file: {e}"
        ) from e


def check_has_audio(src: Path) -> None:
    with open_media(src) as container:
        if not container.streams.audio:
            raise UserFacingError(UPLOAD_WITHOUT_AUDIO, f"{src.name} has no audio track")


def extract_audio(src: Path, dest: Path, rate: int) -> float:
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with open_media(src) as container:
        if not container.streams.audio:
            raise UserFacingError(UPLOAD_WITHOUT_AUDIO, f"{src.name} has no audio track")
        stream = container.streams.audio[0]
        stream.thread_type = "AUTO"
        origin = session_origin(container)
        resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
        n_samples = 0
        with open_mono_wav(str(tmp), rate) as out:
            for frame in container.decode(stream):
                if n_samples == 0 and frame.time is not None:
                    lead = round((frame.time - origin) * rate)
                    if lead > 0:
                        out.writeframes(bytes(SAMPLE_BYTES * lead))
                        n_samples += lead
                for chunk in resampler.resample(frame):
                    n_samples += _write_pcm(out, chunk)
            for chunk in resampler.resample(None):
                n_samples += _write_pcm(out, chunk)
    tmp.replace(dest)
    return n_samples / rate


def _write_pcm(out: wave.Wave_write, frame: av.AudioFrame) -> int:
    pcm = frame.to_ndarray()
    out.writeframes(pcm.tobytes())
    return frame.samples


def ingest_frames(src: Path, paths: SessionPaths, fps: float, max_width: int) -> FrameIndex:
    return rebuild_frames(paths, lambda: extract_frames(src, paths.frames_dir, fps, max_width))


def extract_frames(src: Path, frames_dir: Path, fps: float, max_width: int) -> FrameIndex:
    frames_dir.mkdir(parents=True, exist_ok=True)
    refs: list[FrameRef] = []
    size: tuple[int, int] | None = None
    with open_media(src) as container:
        stream = first_video_stream(container)
        if stream is None:
            return FrameIndex(fps=fps, width=0, height=0, frames=[])
        stream.thread_type = "AUTO"
        origin = session_origin(container)
        sampler = GridSampler(fps)
        for frame in container.decode(stream):
            if frame.time is None:
                continue
            t = max(0.0, frame.time - origin)
            if not sampler.take(t):
                continue
            rotation = frame.rotation % 360
            if size is None:
                size = output_size(frame.width, frame.height, rotation, max_width)
            refs.append(write_frame(frames_dir, t, to_upright_bgr(frame, size, rotation)))
    width, height = size or (0, 0)
    return FrameIndex(fps=fps, width=width, height=height, frames=refs)
