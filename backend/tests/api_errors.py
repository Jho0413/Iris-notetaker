"""OpenAI SDK exceptions built locally, as the SDK raises them, for tests (no network)."""

import httpx2
import openai

REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/chat/completions")


def connection_error(message: str = "Connection error.") -> openai.APIConnectionError:
    return openai.APIConnectionError(message=message, request=REQUEST)


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=REQUEST)


def status_error(
    status: int, code: str | None = None, message: str = "error"
) -> openai.APIStatusError:
    """The SDK's exception for an HTTP error status, e.g. ``RateLimitError`` for 429."""
    body = {"message": message, "type": "error", "code": code}
    response = httpx2.Response(status, request=REQUEST, json={"error": body})
    client = openai.AsyncOpenAI(api_key="test")
    return client._make_status_error(message, body={"error": body}, response=response)
