import asyncio
import logging
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from iris import __version__
from iris.config import TRANSCRIBE_LANGUAGES, Settings
from iris.user_settings import (
    UserSettings,
    apply_user_settings,
    is_pinned,
    save_user_settings,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

LANGUAGE_ENV = "IRIS_TRANSCRIBE_LANGUAGE"


class SettingSource(StrEnum):
    DEFAULT = "default"
    SAVED = "saved"
    PINNED = "pinned"


class LanguageSetting(BaseModel):
    value: str
    default: str
    source: SettingSource
    env_var: str


class AppSettings(BaseModel):
    transcribe_language: LanguageSetting
    languages: list[str]
    version: str
    data_dir: str
    api_key_set: bool


class SettingsUpdate(UserSettings):
    """Fields to change. A field left out (or null) is left as it is."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("transcribe_language", mode="before")
    @classmethod
    def _refuse_blank_language(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError('transcribe_language is empty: use a code such as "en", or "auto"')
        return value


def _view(request: Request) -> AppSettings:
    state = request.app.state
    loaded: Settings = state.loaded_settings
    current: Settings = state.settings
    user: UserSettings = state.user_settings
    if is_pinned(loaded, "transcribe_language"):
        source = SettingSource.PINNED
    elif user.transcribe_language is not None:
        source = SettingSource.SAVED
    else:
        source = SettingSource.DEFAULT
    default = Settings.model_fields["transcribe_language"].default
    return AppSettings(
        transcribe_language=LanguageSetting(
            value=current.transcribe_language or "auto",
            default=default or "auto",
            source=source,
            env_var=LANGUAGE_ENV,
        ),
        languages=sorted(TRANSCRIBE_LANGUAGES),
        version=__version__,
        data_dir=str(current.data_dir.resolve()),
        api_key_set=bool(current.openai_api_key and current.openai_api_key.strip()),
    )


def _lock(request: Request) -> asyncio.Lock:
    return request.app.state.settings_lock


@router.get("", response_model=AppSettings)
def get_app_settings(request: Request) -> AppSettings:
    return _view(request)


@router.put("", response_model=AppSettings)
async def update_app_settings(
    body: SettingsUpdate, request: Request, lock: Annotated[asyncio.Lock, Depends(_lock)]
) -> AppSettings:
    """Save the given settings. 409 if one is pinned by the environment or ``.env``."""
    state = request.app.state
    async with lock:
        loaded: Settings = state.loaded_settings
        changes = body.model_dump(exclude_none=True)
        if "transcribe_language" in changes and is_pinned(loaded, "transcribe_language"):
            raise HTTPException(
                409, f"the spoken language is set by {LANGUAGE_ENV} in the environment or .env"
            )
        if changes:
            user = state.user_settings.model_copy(update=changes)
            try:
                await asyncio.to_thread(save_user_settings, loaded.user_settings_path, user)
            except OSError:
                log.exception("couldn't save %s", loaded.user_settings_path)
                raise HTTPException(500, "the settings couldn't be saved") from None
            settings = apply_user_settings(loaded, user)
            state.user_settings = user
            state.settings = settings
            state.runner.settings = settings
    return _view(request)
