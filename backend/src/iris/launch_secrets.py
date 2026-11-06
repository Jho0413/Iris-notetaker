"""The OpenAI key and API token the desktop app passes on stdin.

``iris serve --secrets-from-stdin`` reads them before the server starts.

Why stdin: a process's environment is readable by any other process of the same user
(``ps eww <pid>``, no prompt), so a key or token there is open to anything running as the user.
A pipe from the app is readable only by the engine. The app writes one JSON line and closes the
pipe; the values go into the ``Settings`` the server is created with, never into ``os.environ``.

Messages about a bad line never include its content: it may hold the key.
"""

from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from iris.config import Settings

# Far above a real line (a key and a token are well under 1 KB): a bigger one is not from the app.
MAX_LINE_BYTES = 16 * 1024


class SecretsError(ValueError):
    """The secrets line is missing or malformed. The message is safe to log."""


class LaunchSecrets(BaseModel):
    """The line the app writes: ``{"openai_api_key": "<key or null>", "api_token": "<token>"}``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    openai_api_key: SecretStr | None  # null: the app has no key (a .env may still supply one)
    api_token: SecretStr

    @field_validator("openai_api_key", "api_token")
    @classmethod
    def _not_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("is empty")
        return value


def _describe(exc: ValidationError) -> str:
    """What is wrong with the line, by field, without any input value."""
    problems = []
    for error in exc.errors(include_input=False, include_url=False):
        if error["type"] == "extra_forbidden":
            problems.append("an unknown field")  # its name came from the line: not repeated
        elif error["loc"]:
            problems.append(f"{error['loc'][0]}: {error['msg']}")
        else:
            problems.append(error["msg"])
    return "the secrets line on stdin is not valid: " + "; ".join(problems)


def read_secrets(stream: BinaryIO) -> LaunchSecrets:
    """Read and check exactly one line from ``stream``. Raises ``SecretsError``."""
    line = stream.readline(MAX_LINE_BYTES + 1)
    if len(line) > MAX_LINE_BYTES:
        raise SecretsError(f"the secrets line on stdin is longer than {MAX_LINE_BYTES} bytes")
    if not line.strip():
        raise SecretsError("no secrets line on stdin (expected one JSON line from the desktop app)")
    try:
        return LaunchSecrets.model_validate_json(line)
    except ValidationError as exc:
        raise SecretsError(_describe(exc)) from None  # no chained error: its text holds the input


def settings_with_secrets(secrets: LaunchSecrets) -> Settings:
    """The process's settings with the app's secrets on top: passed as values, they win over the
    environment and ``.env``, as the app's environment variables did. A null key leaves the key
    to the environment or ``.env``."""
    overrides: dict[str, object] = {"api_token": secrets.api_token}
    if secrets.openai_api_key is not None:
        overrides["openai_api_key"] = secrets.openai_api_key.get_secret_value()
    return Settings(**overrides)
