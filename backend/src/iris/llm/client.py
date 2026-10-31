"""OpenAI client. All model calls go through here so cost and latency can be tracked in one place
(architecture section 5)."""

from functools import lru_cache

from openai import AsyncOpenAI

from iris.config import Settings


class MissingAPIKey(RuntimeError):
    pass


@lru_cache
def _client(api_key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=api_key)


def get_openai(settings: Settings) -> AsyncOpenAI:
    if not settings.openai_api_key:
        raise MissingAPIKey("Set OPENAI_API_KEY in the environment or backend/.env")
    return _client(settings.openai_api_key)
