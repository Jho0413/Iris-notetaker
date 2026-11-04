"""The Settings page: read and save the settings a user can change (``iris/user_settings.py``).

A saved change applies at once, without restarting the engine: the next session started uses it,
and so does a re-run (Try again) that transcribes again; one that resumes after transcription keeps
the transcript it has. A session already recording keeps the language its live transcription
started with.
"""

import asyncio
import logging
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from iris import __version__
from iris.config import TRANSCRIBE_LANGUAGES, Settings, check_language
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
    SAVED = "saved"  # saved from the Settings page
    PINNED = "pinned"  # set by an environment variable or .env, which wins over the page


class LanguageSetting(BaseModel):
    value: str  # what new sessions use: a Whisper language code, or "auto"
    default: str
    source: SettingSource
    env_var: str  # the variable that pins it (named on the page when pinned)


class AppSettings(BaseModel):
    transcribe_language: LanguageSetting
    languages: list[str]  # the Whisper codes the language can be set to (besides "auto")
    version: str
    data_dir: str  # absolute path of the data folder


class SettingsUpdate(BaseModel):
    """Fields to change. A field left out (or null) is left as it is."""

    model_config = ConfigDict(extra="forbid")

    transcribe_language: str | None = None  # a Whisper code, or "auto"

    @field_validator("transcribe_language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():  # "auto" must be asked for, not implied by an empty value
            raise ValueError('transcribe_language is empty: use a code such as "en", or "auto"')
        return check_language(value) or "auto"


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
    async with lock:  # one save at a time, so the file and the running settings agree
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
            state.runner.settings = settings  # the next session and re-run use it
    return _view(request)
