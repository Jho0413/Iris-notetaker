"""Settings, read from environment variables (prefix ``IRIS_``) or ``backend/.env``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", env_file=".env", extra="ignore")

    # Storage: data_dir/iris.db and data_dir/sessions/<id>/
    data_dir: Path = Path("data")

    # OpenAI. Reads OPENAI_API_KEY as well as IRIS_OPENAI_API_KEY.
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    transcribe_model: str = "whisper-1"  # the only OpenAI transcription model with timestamps
    vision_model: str = "gpt-5.5"  # reads the screen; checks rendered visuals
    notes_model: str = "gpt-5.5"
    diagram_model: str = "gpt-5.5"  # writes SVG for exact diagrams, charts and graphs
    image_model: str = "gpt-image-2"  # redraws illustrations from the source crop
    image_quality: Literal["low", "medium", "high"] = "high"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"

    # Rendering: attempts per renderer before falling back, and concurrent visuals.
    render_attempts: int = Field(default=2, ge=1, le=5)
    render_concurrency: int = Field(default=4, ge=1, le=16)

    # Ingest
    frame_fps: float = 1.0  # open question: 1 or 2 fps (architecture section 2)
    frame_max_width: int = 1280
    audio_sample_rate: int = 16_000

    # Selection: bound images and transcript context per vision call.
    selection_batch_size: int = Field(default=6, ge=1, le=20)

    # Serialized evidence characters per notes call (plus prompt and small metadata).
    notes_context_chars: int = Field(default=24_000, ge=2_000, le=60_000)

    # Server
    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "iris.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
