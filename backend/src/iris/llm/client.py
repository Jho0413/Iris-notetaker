"""OpenAI client. All model calls go through here so cost and latency can be tracked in one place.

``get_openai`` returns the SDK client behind a thin proxy: the model-call methods in ``METERED``
report every call (usage, latency, failures) to ``iris.llm.usage``; everything else passes
through unchanged, so stages use it exactly like ``AsyncOpenAI``. ``with_options()`` returns a
metered client too; ``with_raw_response`` / ``with_streaming_response`` are not metered (unused).
"""

import functools
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Any, cast

from openai import AsyncOpenAI

from iris.config import Settings
from iris.llm import usage


class MissingAPIKey(RuntimeError):
    pass


# SDK methods that make a billed model call, by attribute path from the client.
METERED: frozenset[tuple[str, ...]] = frozenset(
    {
        ("chat", "completions", "create"),
        ("chat", "completions", "parse"),
        ("responses", "create"),
        ("responses", "parse"),
        ("images", "generate"),
        ("images", "edit"),
        ("audio", "transcriptions", "create"),
    }
)
_PREFIXES = frozenset(path[:i] for path in METERED for i in range(1, len(path)))


class _Metered:
    """Passes attribute access through to the SDK object, wrapping metered methods."""

    def __init__(self, target: Any, path: tuple[str, ...] = ()):
        self._target = target
        self._path = path

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._target, name)
        path = (*self._path, name)
        if path == ("with_options",):  # a re-configured client is metered too
            return lambda *args, **kwargs: _Metered(attr(*args, **kwargs))
        if path in METERED:
            return _metered(attr, ".".join(path))
        if path in _PREFIXES:
            return _Metered(attr, path)
        return attr


def _metered(call: Callable[..., Awaitable[Any]], operation: str) -> Callable[..., Any]:
    @functools.wraps(call)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        with usage.timed(operation, kwargs) as out:
            out["response"] = await call(*args, **kwargs)
        return out["response"]

    return wrapper


@lru_cache
def _client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key)


def get_openai(settings: Settings) -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise MissingAPIKey("Set OPENAI_API_KEY in the environment or backend/.env")
    return cast(AsyncOpenAI, _Metered(_client(settings.openai_api_key)))
