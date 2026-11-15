from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from iris.llm import structured
from tests.api_errors import connection_error
from tests.builders import stage_ctx


class Answer(BaseModel):
    text: str


def completion(parsed=None, refusal=None, choices=True):
    message = SimpleNamespace(parsed=parsed, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)] if choices else [])


class Model:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = 0

    async def __call__(self) -> Answer:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def accept_unless_bad(answer: Answer) -> str:
    if answer.text == "bad":
        raise ValueError("rejected")
    return answer.text.upper()


async def cached(path, model: Model):
    return await structured.cached(
        path, Answer, accept_unless_bad, model, delays=[0, 0], what="test call"
    )


def test_a_parsed_answer_is_returned():
    assert structured.parsed(completion(parsed=Answer(text="hi")), "m") == Answer(text="hi")


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (completion(choices=False), "m: no response"),
        (completion(refusal="no thanks"), "m: refused: no thanks"),
        (completion(parsed=None), "m: no parsed response"),
    ],
    ids=["no-choices", "refused", "not-parsed"],
)
def test_an_unusable_reply_is_bad_output(reply, message):
    with pytest.raises(ValueError, match=message):
        structured.parsed(reply, "m")


async def test_a_parse_sends_the_prompt_and_content_as_two_messages(tmp_path, monkeypatch):
    sent = {}

    async def parse(**kwargs):
        sent.update(kwargs)
        return completion(parsed=Answer(text="ok"))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(structured, "get_openai", lambda _: client)
    settings = stage_ctx(tmp_path).settings
    answer = await structured.parse(
        settings,
        model="m",
        prompt="system",
        content="user",
        schema=Answer,
        effort="low",
        what="m",
        max_tokens=10,
    )
    assert answer == Answer(text="ok")
    assert sent == {
        "model": "m",
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ],
        "response_format": Answer,
        "reasoning_effort": "low",
        "max_completion_tokens": 10,
    }


async def test_a_parse_without_a_limit_sends_none(tmp_path, monkeypatch):
    sent = {}

    async def parse(**kwargs):
        sent.update(kwargs)
        return completion(parsed=Answer(text="ok"))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(structured, "get_openai", lambda _: client)
    settings = stage_ctx(tmp_path).settings
    await structured.parse(
        settings, model="m", prompt="p", content="c", schema=Answer, effort="low", what="m"
    )
    assert "max_completion_tokens" not in sent


async def test_a_missing_entry_calls_the_model_and_stores_the_answer(tmp_path):
    path = tmp_path / "cache" / "k.json"
    model = Model(Answer(text="fresh"))
    assert await cached(path, model) == "FRESH"
    assert model.calls == 1
    assert Answer.model_validate_json(path.read_text()) == Answer(text="fresh")


async def test_a_valid_entry_is_used_without_a_call(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(Answer(text="kept").model_dump_json())
    model = Model()
    assert await cached(path, model) == "KEPT"
    assert model.calls == 0


async def test_a_damaged_entry_is_replaced_by_a_fresh_answer(tmp_path):
    path = tmp_path / "k.json"
    path.write_text("{not json")
    model = Model(Answer(text="fresh"))
    assert await cached(path, model) == "FRESH"
    assert Answer.model_validate_json(path.read_text()) == Answer(text="fresh")


async def test_an_entry_that_fails_its_check_is_replaced(tmp_path):
    path = tmp_path / "k.json"
    path.write_text(Answer(text="bad").model_dump_json())
    model = Model(Answer(text="fresh"))
    assert await cached(path, model) == "FRESH"
    assert model.calls == 1
    assert Answer.model_validate_json(path.read_text()) == Answer(text="fresh")


async def test_a_fresh_answer_that_fails_its_check_is_raised_and_not_stored(tmp_path):
    path = tmp_path / "k.json"
    with pytest.raises(ValueError, match="rejected"):
        await cached(path, Model(Answer(text="bad")))
    assert not path.exists()


async def test_a_bad_answer_is_raised_at_once_and_not_stored(tmp_path):
    path = tmp_path / "k.json"
    model = Model(ValueError("m: refused: no"))
    with pytest.raises(ValueError, match="refused"):
        await cached(path, model)
    assert model.calls == 1
    assert not path.exists()


async def test_a_temporary_error_is_retried(tmp_path):
    model = Model(connection_error(), Answer(text="fresh"))
    assert await cached(tmp_path / "k.json", model) == "FRESH"
    assert model.calls == 2


def test_the_effort_is_the_first_override_set_else_the_default(tmp_path):
    settings = stage_ctx(tmp_path, reasoning_effort="medium").settings
    assert structured.effort(settings) == "medium"
    assert structured.effort(settings, None, "high") == "high"
    assert structured.effort(settings, "low", "high") == "low"


def test_an_image_part_carries_a_data_url_and_its_detail():
    assert structured.image_part(b"\x01", "image/png") == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,AQ=="},
    }
    assert structured.image_part(b"\x01", "image/jpeg", "high")["image_url"]["detail"] == "high"


def test_the_payload_is_compact_sorted_and_keeps_unicode():
    assert structured.payload_json({"b": "café", "a": 1}) == '{"a": 1, "b": "café"}'
