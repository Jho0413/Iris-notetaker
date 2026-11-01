"""Settings, read from environment variables (prefix ``IRIS_``) or ``backend/.env``."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", env_file=".env", extra="ignore")

    # Storage: data_dir/iris.db and data_dir/sessions/<id>/
    data_dir: Path = Path("data")

    # OpenAI. Reads OPENAI_API_KEY as well as IRIS_OPENAI_API_KEY.
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    # Model choices are an open question (architecture section 5); these are placeholders.
    transcribe_model: str = "whisper-1"
    vision_model: str = "gpt-4o-mini"
    notes_model: str = "gpt-4o"

    # Ingest
    frame_fps: float = 1.0  # open question: 1 or 2 fps (architecture section 2)
    frame_max_width: int = 1280
    audio_sample_rate: int = 16_000

    # Selection: bound images and transcript context per vision call.
    selection_batch_size: int = Field(default=6, ge=1, le=20)

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
