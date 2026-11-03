"""Settings, read from environment variables (prefix ``IRIS_``) or ``backend/.env``."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- Model prices (cost tracking) --------------------------------------------------------------


class ModelPrice(BaseModel):
    """What one model costs, in USD. Token prices are per million tokens.

    A price left as ``None`` is unknown: a call that uses that part of the price is reported as
    unpriced, never guessed (``iris.llm.usage.price_call``).
    """

    input: float | None = None  # text input (and, for chat models, image input)
    cached_input: float | None = None
    output: float | None = None  # text output, including reasoning tokens
    image_input: float | None = None  # image models bill image input tokens separately
    image_output: float | None = None
    audio_input: float | None = None  # token-billed transcription models
    audio_per_second: float | None = None  # duration-billed models, per second rounded up
    # Requests with more than this many input tokens are billed at ``long_context`` instead.
    long_context_above: int | None = None
    long_context: "ModelPrice | None" = None


# Standard-tier prices from https://developers.openai.com/api/docs/pricing, checked on the date
# below. Update both together. Models missing here (e.g. gpt-transcribe, listed only with a
# per-minute estimate) are reported as unpriced.
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
    # Image models: no text output price is listed for gpt-image-2 or gpt-image-1-mini.
    "gpt-image-2": ModelPrice(input=5.00, cached_input=1.25, image_input=8.00, image_output=30.00),
    "gpt-image-1.5": ModelPrice(
        input=5.00, cached_input=1.25, image_input=8.00, image_output=32.00, output=10.00
    ),
    "gpt-image-1-mini": ModelPrice(
        input=2.00, cached_input=0.20, image_input=2.50, image_output=8.00
    ),
    # Transcription. Whisper is $0.006 per minute, billed per second (rounded up). The gpt-4o
    # transcribe models' model pages list audio input at these rates; the pricing page lists the
    # same figure as plain "Input", applied here to text input (the prompt) too.
    "whisper-1": ModelPrice(audio_per_second=0.0001),
    "gpt-4o-transcribe": ModelPrice(input=2.50, audio_input=2.50, output=10.00),
    "gpt-4o-transcribe-diarize": ModelPrice(input=2.50, audio_input=2.50, output=10.00),
    "gpt-4o-mini-transcribe": ModelPrice(input=1.25, audio_input=1.25, output=5.00),
}

Effort = Literal["low", "medium", "high"]


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

    # Live processing while recording (architecture section 1, "Life of a session: live").
    live_processing: bool = True  # off: a live session is processed after stop, like an upload
    live_audio_gap_wait_s: float = Field(default=35.0, gt=0)  # wait for a missing chunk, then skip
    live_window_min_s: float = Field(default=10.0, gt=0)  # transcription windows are cut at the
    live_window_max_s: float = Field(default=30.0, gt=0)  # quietest point between these lengths
    live_transcribe_concurrency: int = Field(default=2, ge=1, le=8)
    live_frame_holdback_s: float = Field(default=2.0, ge=0)  # frames wait this long to be ordered
    live_speech_pad_s: float = Field(default=5.0, ge=0)  # transcript past a moment before select
    live_render_concurrency: int = Field(default=2, ge=1, le=16)
    live_draft_refresh_s: float = Field(default=180.0, gt=0)  # the open tail section, at most
    live_finish_timeout_s: float = Field(default=300.0, gt=0)  # lanes still flushing are dropped

    # Cost controls: reasoning effort per call type; unset uses ``reasoning_effort``.
    select_reasoning_effort: Effort | None = None  # screen reading
    notes_reasoning_effort: Effort | None = None
    diagram_reasoning_effort: Effort | None = None  # SVG figures
    check_reasoning_effort: Effort | None = None  # figure checks

    # Model price table (USD). IRIS_MODEL_PRICES is JSON whose entries add to or replace the
    # defaults, e.g. {"my-model": {"input": 1.0, "output": 4.0}}.
    model_prices: dict[str, ModelPrice] = Field(default_factory=lambda: dict(MODEL_PRICES))

    @field_validator("model_prices", mode="after")
    @classmethod
    def _merge_default_prices(cls, prices: dict[str, ModelPrice]) -> dict[str, ModelPrice]:
        return {**MODEL_PRICES, **prices}

    # Server
    cors_origins: list[str] = ["http://localhost:5173"]

    @model_validator(mode="after")
    def _check_live_windows(self) -> "Settings":
        if self.live_window_min_s >= self.live_window_max_s:
            raise ValueError("live_window_min_s must be shorter than live_window_max_s")
        return self

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "iris.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
