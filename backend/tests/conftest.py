from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from iris import speech
from iris.config import Settings
from iris.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", openai_api_key=None)


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c


def loud_slices(samples: np.ndarray, rate: int) -> np.ndarray:
    """A stand-in for Silero: a slice is voice (1.0) if any sample in it is loud."""
    n = round(speech.SLICE_S * rate)
    count = len(samples) // n
    peaks = np.abs(samples[: count * n].astype(np.int32)).reshape(count, n).max(axis=1)
    return (peaks > 1000).astype(np.float32)


@pytest.fixture(autouse=True)
def loud_is_voice(monkeypatch):
    """Silero hears no voice in the tests' synthetic tones, so in tests anything loud is voice.
    Tests of the real model call ``test_speech.SILERO`` (saved before this patch) directly."""
    monkeypatch.setattr(speech, "voice_probabilities", loud_slices)


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Iris's retries of temporary API errors wait no time in tests (three retries, as by
    default). Tests that need other delays pass ``api_retry_delays_s`` to ``Settings``."""
    monkeypatch.setenv("IRIS_API_RETRY_DELAYS_S", "[0, 0, 0]")
