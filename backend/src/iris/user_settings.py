import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from iris.config import Settings, check_language, get_settings
from iris.store.layout import write_model

log = logging.getLogger(__name__)


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    transcribe_language: str | None = None

    @field_validator("transcribe_language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return check_language(value) or "auto"


def is_pinned(settings_as_loaded: Settings, field: str) -> bool:
    return field in settings_as_loaded.model_fields_set


def load_user_settings(path: Path) -> UserSettings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return UserSettings()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        log.warning("ignoring %s: can't read it (%s)", path, exc)
        return UserSettings()
    if not isinstance(raw, dict):
        log.warning("ignoring %s: expected a JSON object", path)
        return UserSettings()
    saved: dict[str, object] = {}
    for field in UserSettings.model_fields:
        if field not in raw:
            continue
        try:
            UserSettings.model_validate({field: raw[field]})
        except ValidationError:
            log.warning("ignoring %s in %s: %r is not a valid value", field, path, raw[field])
            continue
        saved[field] = raw[field]
    return UserSettings.model_validate(saved)


def save_user_settings(path: Path, user: UserSettings) -> None:
    write_model(path, user)


def apply_user_settings(settings: Settings, user: UserSettings) -> Settings:
    update: dict[str, object] = {}
    if user.transcribe_language is not None and not is_pinned(settings, "transcribe_language"):
        update["transcribe_language"] = check_language(user.transcribe_language)
    return settings.model_copy(update=update) if update else settings


def load_settings() -> Settings:
    settings = get_settings()
    return apply_user_settings(settings, load_user_settings(settings.user_settings_path))
