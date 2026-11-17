from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WHISPER_CODES = (
    "af am ar as az ba be bg bn bo br bs ca cs cy da de el en es et eu fa fi fo fr gl gu ha "
    "he hi hr ht hu hy id is it ja jw ka kk km kn ko la lb ln lo lt lv mg mi mk ml mn mr ms "
    "mt my ne nl nn no oc pa pl ps pt ro ru sa sd si sk sl sn so sq sr su sv sw ta te tg th "
    "tk tl tr tt uk ur uz vi yi yo zh"
)
TRANSCRIBE_LANGUAGES = frozenset(_WHISPER_CODES.split())


def check_language(value: object) -> str | None:
    if value is None:
        return None
    code = str(value).strip().lower()
    if code in ("", "auto"):
        return None
    if code not in TRANSCRIBE_LANGUAGES:
        raise ValueError(
            f'transcribe_language "{code}" is not a language Whisper knows: use an ISO-639-1 '
            'code such as "en", "fr" or "ja", or "auto"'
        )
    return code


class ModelPrice(BaseModel):
    input: float | None = None
    cached_input: float | None = None
    output: float | None = None
    image_input: float | None = None
    image_output: float | None = None
    audio_input: float | None = None
    audio_per_second: float | None = None
    long_context_above: int | None = None
    long_context: ModelPrice | None = None


PRICES_CHECKED = "2026-11-03"
MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-5.5": ModelPrice(
        input=5.00,
        cached_input=0.50,
        output=30.00,
        long_context_above=272_000,
        long_context=ModelPrice(input=10.00, cached_input=1.00, output=45.00),
    ),
    "gpt-5.4": ModelPrice(
        input=2.50,
        cached_input=0.25,
        output=15.00,
        long_context_above=272_000,
        long_context=ModelPrice(input=5.00, cached_input=0.50, output=22.50),
    ),
    "gpt-5.4-mini": ModelPrice(input=0.75, cached_input=0.075, output=4.50),
    "gpt-5": ModelPrice(input=1.25, cached_input=0.125, output=10.00),
    "gpt-5-mini": ModelPrice(input=0.25, cached_input=0.025, output=2.00),
    "gpt-5-nano": ModelPrice(input=0.05, cached_input=0.005, output=0.40),
    "gpt-4.1": ModelPrice(input=2.00, cached_input=0.50, output=8.00),
    "gpt-4.1-mini": ModelPrice(input=0.40, cached_input=0.10, output=1.60),
    "gpt-4o": ModelPrice(input=2.50, cached_input=1.25, output=10.00),
    "gpt-4o-mini": ModelPrice(input=0.15, cached_input=0.075, output=0.60),
    "gpt-image-2": ModelPrice(input=5.00, cached_input=1.25, image_input=8.00, image_output=30.00),
    "gpt-image-1.5": ModelPrice(
        input=5.00, cached_input=1.25, image_input=8.00, image_output=32.00, output=10.00
    ),
    "gpt-image-1-mini": ModelPrice(
        input=2.00, cached_input=0.20, image_input=2.50, image_output=8.00
    ),
    "whisper-1": ModelPrice(audio_per_second=0.0001),
    "gpt-4o-transcribe": ModelPrice(input=2.50, audio_input=2.50, output=10.00),
    "gpt-4o-transcribe-diarize": ModelPrice(input=2.50, audio_input=2.50, output=10.00),
    "gpt-4o-mini-transcribe": ModelPrice(input=1.25, audio_input=1.25, output=5.00),
}

Effort = Literal["low", "medium", "high"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IRIS_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")

    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    transcribe_model: str = "whisper-1"
    transcribe_language: str | None = "en"
    vision_model: str = "gpt-5.5"
    notes_model: str = "gpt-5.5"
    diagram_model: str = "gpt-5.5"
    image_model: str = "gpt-image-2"
    image_quality: Literal["low", "medium", "high"] = "high"
    reasoning_effort: Literal["low", "medium", "high"] = "medium"

    render_attempts: int = Field(default=2, ge=1, le=5)
    render_concurrency: int = Field(default=4, ge=1, le=16)

    api_retry_delays_s: list[float] = Field(default_factory=lambda: [15.0, 60.0, 120.0])

    frame_fps: float = 1.0
    frame_max_width: int = 1280
    audio_sample_rate: int = Field(default=16_000, ge=8_000, le=16_000)

    selection_batch_size: int = Field(default=6, ge=1, le=20)

    notes_context_chars: int = Field(default=24_000, ge=2_000, le=60_000)

    synthesis: bool = True
    synthesis_model: str = "gpt-5.5"
    synthesis_plan_reasoning_effort: Effort = "low"
    synthesis_reasoning_effort: Effort | None = None

    live_processing: bool = True
    live_audio_gap_wait_s: float = Field(default=35.0, gt=0)
    live_window_min_s: float = Field(default=5.0, gt=0)
    live_window_max_s: float = Field(default=15.0, gt=0)
    live_transcribe_concurrency: int = Field(default=2, ge=1, le=8)
    live_frame_holdback_s: float = Field(default=2.0, ge=0)
    live_speech_pad_s: float = Field(default=5.0, ge=0)
    live_render_concurrency: int = Field(default=2, ge=1, le=16)
    live_finish_timeout_s: float = Field(default=300.0, gt=0)

    select_reasoning_effort: Effort | None = None
    notes_reasoning_effort: Effort | None = None
    diagram_reasoning_effort: Effort | None = None
    check_reasoning_effort: Effort | None = None

    model_prices: dict[str, ModelPrice] = Field(default_factory=lambda: dict(MODEL_PRICES))

    @field_validator("model_prices", mode="after")
    @classmethod
    def _merge_default_prices(cls, prices: dict[str, ModelPrice]) -> dict[str, ModelPrice]:
        return {**MODEL_PRICES, **prices}

    cors_origins: list[str] = ["http://localhost:5173"]
    api_token: SecretStr | None = None
    parent_pid: int | None = Field(default=None, gt=0)
    parent_poll_s: float = Field(default=1.0, gt=0, le=60)

    @field_validator("api_token", mode="before")
    @classmethod
    def _empty_token_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("transcribe_language", mode="before")
    @classmethod
    def _check_language(cls, value: object) -> str | None:
        return check_language(value)

    @field_validator("api_retry_delays_s", mode="after")
    @classmethod
    def _check_retry_delays(cls, delays: list[float]) -> list[float]:
        if len(delays) > 10 or any(not 0 <= d <= 600 for d in delays):
            raise ValueError("api_retry_delays_s takes up to 10 delays of 0 to 600 seconds")
        return delays

    @model_validator(mode="after")
    def _check_live_windows(self) -> Settings:
        if self.live_window_min_s >= self.live_window_max_s:
            raise ValueError("live_window_min_s must be shorter than live_window_max_s")
        return self

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "iris.db"

    @property
    def user_settings_path(self) -> Path:
        return self.data_dir / "settings.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
