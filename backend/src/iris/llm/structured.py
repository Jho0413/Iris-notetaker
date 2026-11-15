import base64
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Literal

from openai.types.chat import ChatCompletionContentPartImageParam, ChatCompletionContentPartParam
from openai.types.chat.chat_completion_content_part_image_param import ImageURL
from pydantic import BaseModel

from iris.config import Effort, Settings
from iris.llm.client import get_openai
from iris.llm.errors import retry_temporary
from iris.store.layout import read_model, write_model

MAX_COMPLETION_TOKENS = 32_000

Content = str | list[ChatCompletionContentPartParam]


def payload_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def request_key(prompt: str, model: str, schema: type[BaseModel], payload: str) -> str:
    request = {
        "prompt": prompt,
        "model": model,
        "schema": schema.model_json_schema(),
        "payload": payload,
    }
    return hashlib.sha256(payload_json(request).encode()).hexdigest()


def effort(settings: Settings, *overrides: Effort | None) -> Effort:
    return next((chosen for chosen in overrides if chosen), settings.reasoning_effort)


def data_url(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def image_part(
    data: bytes, mime: str, detail: Literal["auto", "low", "high"] | None = None
) -> ChatCompletionContentPartImageParam:
    image_url: ImageURL = {"url": data_url(data, mime)}
    if detail is not None:
        image_url["detail"] = detail
    return {"type": "image_url", "image_url": image_url}


def parsed[M: BaseModel](completion, what: str) -> M:
    if not completion.choices:
        raise ValueError(f"{what}: no response")
    message = completion.choices[0].message
    if message.refusal:
        raise ValueError(f"{what}: refused: {message.refusal}")
    if message.parsed is None:
        raise ValueError(f"{what}: no parsed response")
    return message.parsed


async def parse[M: BaseModel](
    settings: Settings,
    *,
    model: str,
    prompt: str,
    content: Content,
    schema: type[M],
    effort: Effort,
    what: str,
    max_tokens: int | None = None,
) -> M:
    limit = {} if max_tokens is None else {"max_completion_tokens": max_tokens}
    completion = await get_openai(settings).chat.completions.parse(
        model=model,
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
        response_format=schema,
        reasoning_effort=effort,
        **limit,
    )
    return parsed(completion, what)


async def cached[M: BaseModel, R](
    path: Path,
    schema: type[M],
    accept: Callable[[M], R],
    make: Callable[[], Awaitable[M]],
    *,
    delays: Sequence[float],
    what: str,
) -> R:
    try:
        return accept(read_model(path, schema))
    except (FileNotFoundError, ValueError):
        pass
    fresh = await retry_temporary(make, delays, what=what)
    accepted = accept(fresh)
    path.parent.mkdir(exist_ok=True)
    write_model(path, fresh)
    return accepted
