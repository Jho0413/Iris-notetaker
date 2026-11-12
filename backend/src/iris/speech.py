"""Speech stretches: the parts of a piece of audio where someone is talking.

Whisper writes text over audio that has no speech in it (silence, room noise, clicks): "Thank you
for watching", or the sentence before it again. So only the speech is sent: Silero VAD scores the
audio (``voice_probabilities``) and the scores become speech stretches (``speech_stretches``). Each
stretch is sent on its own, as WhisperX does, so a line's time is the stretch's start plus
Whisper's time; joining stretches into one clip would let Whisper's loose line ends slide across a
join, 2 s or more later in the recording. A piece with no speech is not sent at all.

Two questions, each answered by a tool's documented defaults rather than tuned here:

- **What is speech:** Silero's own rule (``get_speech_timestamps``): speech starts at a score of
  0.5, ends once the score stays under 0.35 for 100 ms, and a run of 250 ms or less is dropped. A
  click can score high for a slice or two, but never that long.
- **What is cut out:** only gaps of 2 s or more between speech, each stretch padded by 400 ms
  (faster-whisper's ``VadOptions`` for transcription), so the speech keeps its natural pauses.
"""

import numpy as np
from pysilero_vad import SileroVoiceActivityDetector

SILERO_RATE = 16_000  # the model's sample rate
SLICE = 512  # samples Silero scores at a time, at SILERO_RATE
SLICE_S = SLICE / SILERO_RATE  # 32 ms

# Silero's get_speech_timestamps defaults.
SPEECH_START = 0.5
SPEECH_END = 0.35  # its neg_threshold: threshold - 0.15
SPEECH_END_AFTER_S = 0.1
MIN_SPEECH_S = 0.25

# faster-whisper's VadOptions defaults.
MIN_GAP_S = 2.0
PAD_S = 0.4


def voice_probabilities(samples: np.ndarray, rate: int) -> np.ndarray:
    """Silero's speech probability for each ``SLICE_S`` of int16 ``samples``; a trailing part
    slice is not scored. Audio at another rate is resampled for scoring only."""
    x = samples.astype(np.float32) / 32768
    if rate != SILERO_RATE and len(x):
        t = np.arange(round(len(x) * SILERO_RATE / rate)) / SILERO_RATE
        x = np.interp(t, np.arange(len(x)) / rate, x).astype(np.float32)
    vad = SileroVoiceActivityDetector()  # ~1 ms to load; one per call keeps calls thread-safe
    return np.array(
        [vad.process_samples(x[i : i + SLICE]) for i in range(0, len(x) - SLICE + 1, SLICE)],
        dtype=np.float32,
    )


def speech_runs(probs: np.ndarray) -> list[tuple[float, float]]:
    """Silero's speech runs, in seconds, from per-slice probabilities."""
    runs: list[tuple[float, float]] = []
    start: float | None = None
    quiet_since: float | None = None  # when the score last fell under SPEECH_END
    for i, p in enumerate(probs):
        t = i * SLICE_S
        if start is None:
            if p >= SPEECH_START:
                start = t
        elif p >= SPEECH_START:
            quiet_since = None
        elif p < SPEECH_END:
            if quiet_since is None:
                quiet_since = t
            if t - quiet_since >= SPEECH_END_AFTER_S:
                if quiet_since - start > MIN_SPEECH_S:
                    runs.append((start, quiet_since))
                start = quiet_since = None
    end = len(probs) * SLICE_S
    if start is not None and end - start > MIN_SPEECH_S:
        runs.append((start, end))
    return runs


def speech_stretches(probs: np.ndarray, n_samples: int, rate: int) -> list[tuple[int, int]]:
    """What to send, as ``(start, end)`` sample ranges of the piece: Silero's runs, merged where
    less than ``MIN_GAP_S`` apart, padded by ``PAD_S`` and kept within the piece. In order and
    apart, since every gap left is longer than both paddings."""
    merged: list[tuple[float, float]] = []
    for a, b in speech_runs(probs):
        if merged and a - merged[-1][1] < MIN_GAP_S:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return [
        (max(0, round((a - PAD_S) * rate)), min(n_samples, round((b + PAD_S) * rate)))
        for a, b in merged
    ]


def find_speech(samples: np.ndarray, rate: int) -> list[tuple[int, int]]:
    """The speech stretches of int16 ``samples``, as sample ranges; empty if there is no speech."""
    return speech_stretches(voice_probabilities(samples, rate), len(samples), rate)
