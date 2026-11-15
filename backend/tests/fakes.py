import asyncio
import json
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI

from iris.llm import client as llm_client
from iris.pipeline.stages import render, select, synthesize
from tests.builders import section

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20"/></svg>'
SPOKEN = "Ship the prototype. Alex will test it."


def parsed_response(result, refusal: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(parsed=result, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def fake_openai(parse=None, transcribe=None) -> SimpleNamespace:
    fake = SimpleNamespace()
    if parse is not None:
        fake.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))
    if transcribe is not None:
        fake.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe))
    return fake


def install_client(monkeypatch, fake) -> None:
    monkeypatch.setattr(llm_client, "_client", lambda _key: fake)


def install_get_openai(monkeypatch, fake, *modules) -> None:
    for module in modules:
        monkeypatch.setattr(module, "get_openai", lambda _: fake)


class PipelineOpenAI:
    def __init__(self):
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.transcribe))

    async def transcribe(self, **kwargs):
        return {"language": "english", "segments": [{"start": 0, "end": 2, "text": SPOKEN}]}

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.respond(self.answer(kwargs), kwargs)

    def respond(self, result, kwargs) -> SimpleNamespace:
        return parsed_response(result)

    def check(self, kwargs) -> render.Check:
        return render.Check(matches=True, problems=[])

    def answer(self, kwargs):
        schema = kwargs.get("response_format")
        if schema is None:
            return None
        if schema is select.Decisions:
            content = kwargs["messages"][1]["content"]
            ids = [json.loads(p["text"])["moment_id"] for p in content[1:] if p["type"] == "text"]
            figure = select.ElementDecision(
                kind="figure",
                content="A coloured slide",
                build=0,
                region=None,
                caption="Coloured slide",
                render_as="svg",
            )
            return select.Decisions(
                decisions=[select.Decision(moment_id=m, keep=True, elements=[figure]) for m in ids]
            )
        if schema is render.SvgDrawing:
            return render.SvgDrawing(svg=SVG)
        if schema is render.Check:
            return self.check(kwargs)
        if schema is synthesize.EditPlan:
            return synthesize.EditPlan(edits=[])
        payload = json.loads(kwargs["messages"][1]["content"])
        return section([r["id"] for r in payload["records"] if r["kind"] == "visual"])


class Drawer:
    def __init__(self, monkeypatch):
        self.errors: dict[str, BaseException] = {}
        self.calls: list[str] = []
        self.hold: dict[str, asyncio.Event] = {}
        self.cancelled: list[str] = []
        monkeypatch.setitem(render.DRAWERS, "svg", (self.draw, "svg"))
        monkeypatch.setitem(render.DRAWERS, "image", (self.draw, "png"))
        monkeypatch.setattr(render, "check", self.check)

    async def draw(self, _ctx, visual, _source, _feedback):
        self.calls.append(visual.id)
        try:
            if visual.id in self.hold:
                await self.hold[visual.id].wait()
        except asyncio.CancelledError:
            self.cancelled.append(visual.id)
            raise
        if visual.id in self.errors:
            raise self.errors[visual.id]
        return SVG.encode(), b"preview"

    async def check(self, *_args):
        return render.Check(matches=True, problems=[])


def answer(content: str) -> dict:
    return {"role": "assistant", "content": content}


def refusal(text: str) -> dict:
    return {"role": "assistant", "content": None, "refusal": text}


def chat_completion(*messages: dict, model: str = "m") -> httpx.Response:
    choices = [{"index": i, "finish_reason": "stop", "message": m} for i, m in enumerate(messages)]
    body = {"id": "test", "object": "chat.completion", "created": 0, "model": model}
    return httpx.Response(200, json={**body, "choices": choices})


def sdk_client(handle) -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        max_retries=0,
    )
