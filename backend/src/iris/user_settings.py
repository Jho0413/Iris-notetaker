"""Settings saved from the UI's Settings page, kept in ``<data_dir>/settings.json``.

Precedence, highest first:

1. A value set explicitly: an ``IRIS_*`` environment variable, ``.env``, or a keyword passed to
   ``Settings``. It is *pinned*: the page shows it and can't change it, so ``make dev``, the CLI and
   tests behave as they did before this file existed.
2. The value saved here.
3. The default in ``Settings``.

Only the fields of ``UserSettings`` can be saved. A damaged file never stops Iris starting: an
unreadable file is ignored as a whole and an invalid value on its own, each with a warning, and the
next save replaces the file.
"""

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from iris.config import Settings, check_language, get_settings
from iris.store.layout import write_model

log = logging.getLogger(__name__)


class UserSettings(BaseModel):
    """What the Settings page saved. ``None`` means nothing saved: the default applies."""

    model_config = ConfigDict(extra="ignore")  # keys from a newer or older Iris are skipped

    # A Whisper language code, or "auto" for the model to detect it.
    transcribe_language: str | None = None

    @field_validator("transcribe_language")
    @classmethod
    def _check_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return check_language(value) or "auto"


def is_pinned(settings: Settings, field: str) -> bool:
    """Whether ``field`` was set explicitly (environment, ``.env`` or a keyword), so it wins.

    Ask the settings as loaded, before ``apply_user_settings``: applying marks fields as set too.
    """
    return field in settings.model_fields_set


def load_user_settings(path: Path) -> UserSettings:
    """The saved settings, or none if the file is missing or damaged (logged, never raised)."""
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
    """Write the file atomically, so a crash never leaves it half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    write_model(path, user)


def apply_user_settings(settings: Settings, user: UserSettings) -> Settings:
    """``settings`` with the saved values applied, except where a value is pinned.

    Always apply to the settings as loaded (see ``is_pinned``), not to an earlier result.
    """
    update: dict[str, object] = {}
    if user.transcribe_language is not None and not is_pinned(settings, "transcribe_language"):
        update["transcribe_language"] = check_language(user.transcribe_language)
    return settings.model_copy(update=update) if update else settings


def load_settings() -> Settings:
    """The process's settings with the saved values applied (the CLI's view; the API keeps the
    loaded settings too, to tell pinned values apart)."""
    settings = get_settings()
    return apply_user_settings(settings, load_user_settings(settings.user_settings_path))
