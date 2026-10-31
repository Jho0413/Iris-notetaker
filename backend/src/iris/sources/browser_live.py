"""Browser live source: audio chunks and screen frames posted by the UI while recording.

The browser sends MediaRecorder audio chunks every few seconds and JPEG screen frames at a low
fixed rate. They are appended to the session as they arrive, so the session is its own recording
and can be fully re-processed afterwards like an upload (architecture section 1, live).
"""

from iris.pipeline.context import StageContext
from iris.store.layout import SessionPaths


def append_audio_chunk(paths: SessionPaths, seq: int, data: bytes) -> None:
    paths.live_audio_dir.mkdir(parents=True, exist_ok=True)
    (paths.live_audio_dir / f"{seq:06d}.webm").write_bytes(data)


def append_frame(paths: SessionPaths, t: float, data: bytes) -> str:
    paths.frames_dir.mkdir(parents=True, exist_ok=True)
    name = f"{round(t * 1000):09d}.jpg"
    (paths.frames_dir / name).write_bytes(data)
    return f"frames/{name}"


class BrowserLiveSource:
    async def ingest(self, ctx: StageContext) -> None:
        """Finalise a live recording into the standard layout.

        TODO:
          - concatenate live/audio/*.webm in order (MediaRecorder timeslice chunks concatenate
            into one valid stream), decode with PyAV and resample to audio.wav
          - build frames.json from frames/*.jpg (timestamp is in the filename)
        """
        raise NotImplementedError("browser live ingest")
