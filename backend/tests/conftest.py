from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", openai_api_key=None)


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Iris's retries of temporary API errors wait no time in tests (three retries, as by
    default). Tests that need other delays pass ``api_retry_delays_s`` to ``Settings``."""
    monkeypatch.setenv("IRIS_API_RETRY_DELAYS_S", "[0, 0, 0]")
