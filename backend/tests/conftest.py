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
    n = round(speech.SLICE_S * rate)
    count = len(samples) // n
    peaks = np.abs(samples[: count * n].astype(np.int32)).reshape(count, n).max(axis=1)
    return (peaks > 1000).astype(np.float32)


@pytest.fixture(autouse=True)
def loud_is_voice(monkeypatch):
    monkeypatch.setattr(speech, "voice_probabilities", loud_slices)


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    monkeypatch.setenv("IRIS_API_RETRY_DELAYS_S", "[0, 0, 0]")
