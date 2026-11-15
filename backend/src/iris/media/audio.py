import io
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

SAMPLE_BYTES = 2
QUIET_RATIO = 2.0
TRANSCRIPTION_UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024
WAV_HEADER_ALLOWANCE_BYTES = 1024
POWER_BLOCK_SAMPLES = 4_000_000


@contextmanager
def open_mono_wav(file: str | BinaryIO, rate: int) -> Iterator[wave.Wave_write]:
    with wave.open(file, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_BYTES)
        out.setframerate(rate)
        yield out


def encode_wav(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with open_mono_wav(buf, rate) as w:
        w.writeframes(samples.astype("<i2", copy=False).tobytes())
    return buf.getvalue()


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != SAMPLE_BYTES or w.getnchannels() != 1:
            raise ValueError(
                f"{path}: expected 16-bit mono PCM, got {w.getsampwidth() * 8}-bit "
                f"{w.getnchannels()}-channel"
            )
        rate = w.getframerate()
        data = w.readframes(w.getnframes())
    return np.frombuffer(data, dtype="<i2"), rate


def fits_transcription_upload(samples: float) -> bool:
    wav_bytes = samples * SAMPLE_BYTES + WAV_HEADER_ALLOWANCE_BYTES
    return wav_bytes <= TRANSCRIPTION_UPLOAD_LIMIT_BYTES


def _power_per_frame_in_blocks(samples: np.ndarray, frame: int) -> np.ndarray:
    n_frames = len(samples) // frame
    power = np.empty(n_frames, dtype=np.float64)
    block = max(1, POWER_BLOCK_SAMPLES // frame)
    for i in range(0, n_frames, block):
        j = min(i + block, n_frames)
        x = samples[i * frame : j * frame].astype(np.float32).reshape(j - i, frame)
        power[i:j] = np.mean(np.square(x), axis=1)
    return power


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


@dataclass(frozen=True)
class QuietProfile:
    quiet: np.ndarray
    frame: int
    first_centre: int

    def quietest(self, lo: int, hi: int, target: int) -> int | None:
        j_lo = max(0, _ceil_div(lo - self.first_centre, self.frame))
        j_hi = min(len(self.quiet) - 1, (hi - self.first_centre) // self.frame)
        if j_lo > j_hi:
            return None
        window = self.quiet[j_lo : j_hi + 1]
        floor = window.min()
        candidates = np.flatnonzero(window <= floor * QUIET_RATIO + 1e-12) + j_lo
        centres = candidates * self.frame + self.first_centre
        return int(centres[np.argmin(np.abs(centres - target))])


def quiet_profile(
    samples: np.ndarray, rate: int, window_s: float = 0.05, pause_s: float = 0.3
) -> QuietProfile:
    frame = max(1, int(window_s * rate))
    frames_per_pause = max(1, round(pause_s / window_s))
    power = _power_per_frame_in_blocks(samples, frame)
    if len(power) >= frames_per_pause:
        csum = np.concatenate(([0.0], np.cumsum(power)))
        quiet = (csum[frames_per_pause:] - csum[:-frames_per_pause]) / frames_per_pause
    else:
        quiet = np.empty(0)
    return QuietProfile(quiet, frame, frames_per_pause * frame // 2)
