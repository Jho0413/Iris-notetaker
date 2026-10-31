"""Upload source: a recording file saved as ``source.<ext>``."""

import shutil
from pathlib import Path
from typing import BinaryIO

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
        """Faster than real time (architecture section 1, step 2).

        TODO:
          - audio: PyAV decode the audio stream, av.AudioResampler to s16 mono 16 kHz, write
            audio.wav (stdlib ``wave``)
          - frames: PyAV decode, keep one frame every 1/frame_fps seconds, downscale to
            frame_max_width, save frames/<t_ms>.jpg, write FrameIndex to frames.json
          - audio-only uploads: write an empty FrameIndex so later stages still run
          - run audio and frames concurrently, each in asyncio.to_thread (decoding is CPU bound)
        """
        if ctx.paths.find_source() is None:
            raise FileNotFoundError("no source.* file in session")
        raise NotImplementedError("upload ingest")
