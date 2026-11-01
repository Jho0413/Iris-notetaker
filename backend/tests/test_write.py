import json
from unittest.mock import AsyncMock

import pytest

from iris.config import Settings
from iris.models import (
    EventType,
    FrameRef,
    Notes,
    NotesDraft,
    Selection,
    SessionType,
    SourceKind,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.context import StageContext
from iris.pipeline.stages import assemble, write
from iris.store.layout import SessionPaths, read_model, write_model


def section(ids=(), text="## Decisions\n\nShip the prototype. Alex will test it."):
    return write.WrittenSection(
        session_type=SessionType.MEETING,
        title="Prototype review",
        blocks=[write.NoteBlock(markdown=text, visual_ids=list(ids))],
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    events = []
    ctx = StageContext(
        session_id="s1",
        source=SourceKind.UPLOAD,
        paths=SessionPaths(tmp_path).ensure(),
        settings=Settings(data_dir=tmp_path),
        emit=lambda kind, data: events.append((kind, data)),
    )
    transcript = Transcript(
        segments=[
            TranscriptSegment(
                start=0,
                end=10,
                text="We agreed to ship the prototype. Alex will test it.",
                speaker="Sam",
            )
        ]
    )
    visual = Visual(
        id="v1",
        moment_id="m1",
        frame=FrameRef(t=1, path="frames/1.jpg"),
        caption="Prototype [UI]",
        path="visuals/v1.jpg",
    )
    write_model(ctx.paths.transcript, transcript)
    write_model(ctx.paths.selection, Selection(visuals=[visual]))
    generate = AsyncMock(return_value=section(["v1"]))
    monkeypatch.setattr(write, "generate_section", generate)
    return ctx, transcript, visual, generate, events


async def test_writes_draft_assembles_and_reuses_cache(setup):
    ctx, _, _, generate, events = setup
    await write.run(ctx)
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.markdown.endswith("[visual:v1]")
    payload = json.loads(generate.call_args.args[1])
    assert payload["records"][0]["speaker"] == "Sam"
    assert payload["records"][1]["caption"] == "Prototype [UI]"
    await assemble.run(ctx)
    notes = read_model(ctx.paths.notes, Notes)
    assert "visuals/v1.jpg" in notes.markdown
    assert "[visual:" not in notes.markdown
    assert notes.session_type == SessionType.MEETING
    assert events[0] == (
        EventType.NOTES_UPDATED,
        {
            "final": False,
            "completed_parts": 1,
            "total_parts": 1,
        },
    )
    await write.run(ctx)
    assert generate.await_count == 1
    cache = next((ctx.paths.root / "notes_sections").glob("*.json"))
    cache.write_text("{damaged")
    await write.run(ctx)
    assert generate.await_count == 2


async def test_cache_invalidates_input_and_model_changes(setup):
    ctx, transcript, visual, generate, _ = setup
    await write.run(ctx)
    transcript.segments[0].text += " Correction: ship next week."
    write_model(ctx.paths.transcript, transcript)
    await write.run(ctx)
    visual.caption += " revised"
    write_model(ctx.paths.selection, Selection(visuals=[visual]))
    await write.run(ctx)
    ctx.settings.notes_model = "different-model"
    await write.run(ctx)
    assert generate.await_count == 4


async def test_long_transcript_preserved_and_failed_parts_resume(setup):
    ctx, transcript, _, generate, _ = setup
    ctx.settings.notes_context_chars = 2000
    original = '日本語 "quoted" \\ ' * 500
    transcript.segments[0].text = original
    write_model(ctx.paths.transcript, transcript)
    write_model(ctx.paths.selection, Selection(visuals=[]))
    parts = write.plan_parts(transcript, Selection(visuals=[]), 2000)
    assert len(parts) > 2
    assert "".join(r["text"] for p in parts for r in p.records) == original
    assert all(len(write._json(p.records)) <= 2000 for p in parts)
    generate.side_effect = [section(), RuntimeError("offline")]
    with pytest.raises(RuntimeError, match="offline"):
        await write.run(ctx)
    assert read_model(ctx.paths.notes_draft, NotesDraft).markdown == section().blocks[0].markdown
    generate.reset_mock(side_effect=True)
    generate.return_value = section()
    await write.run(ctx)
    assert generate.await_count == len(parts) - 1
    payload = json.loads(generate.call_args.args[1])
    assert payload["session_type"] == "meeting"
    assert payload["title"] == "Prototype review"


@pytest.mark.parametrize(
    "bad",
    [
        section(["unknown"]),
        section([]),
        section(["v1"], ""),
        section(["v1"], "![invented](https://example.com/a.jpg)"),
        section(["v1"], "<img src='a.jpg'>"),
        section(["v1"], "[visual:v1]"),
        section(["v1"], "```python\nprint(1)"),
        write.WrittenSection(session_type=SessionType.DEMO, title="", blocks=[]),
    ],
)
async def test_invalid_output_is_not_cached_or_published(setup, bad):
    ctx, _, _, generate, events = setup
    generate.return_value = bad
    with pytest.raises(ValueError):
        await write.run(ctx)
    assert not ctx.paths.notes_draft.exists()
    assert not list((ctx.paths.root / "notes_sections").glob("*.json"))
    assert events == []


@pytest.mark.parametrize("kind", list(SessionType))
async def test_session_styles_and_audio_only(setup, kind):
    ctx, _, _, generate, _ = setup
    write_model(ctx.paths.selection, Selection(visuals=[]))
    result = section()
    result.session_type = kind
    generate.return_value = result
    await write.run(ctx)
    assert read_model(ctx.paths.notes_draft, NotesDraft).session_type == kind


async def test_visual_only_and_empty_input(setup):
    ctx, _, _, generate, _ = setup
    write_model(ctx.paths.transcript, Transcript(segments=[]))
    await write.run(ctx)
    assert json.loads(generate.call_args.args[1])["records"][0]["kind"] == "visual"
    write_model(ctx.paths.selection, Selection(visuals=[]))
    with pytest.raises(ValueError, match="no transcript"):
        await write.run(ctx)
    assert generate.await_count == 1


def test_changed_style_rejected():
    first = section()
    changed = section()
    changed.session_type = SessionType.LECTURE
    with pytest.raises(ValueError, match="retain"):
        write.validate_section(changed, write.WritingPart([]), first)


@pytest.mark.parametrize("mode", ["ok", "refusal", "empty", "invalid"])
async def test_real_sdk_response_parsing(tmp_path, monkeypatch, mode):
    import httpx
    from openai import AsyncOpenAI
    from pydantic import ValidationError

    ctx = StageContext(
        session_id="s", source=SourceKind.UPLOAD, paths=SessionPaths(tmp_path), settings=Settings()
    )

    def handle(request):
        payload = json.loads(request.content)
        assert payload["response_format"]["json_schema"]["strict"]
        content = section().model_dump_json() if mode == "ok" else "{}"
        message = {"role": "assistant", "content": content}
        if mode == "refusal":
            message = {"role": "assistant", "content": None, "refusal": "Cannot comply"}
        choices = (
            [] if mode == "empty" else [{"index": 0, "finish_reason": "stop", "message": message}]
        )
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": ctx.settings.notes_model,
                "choices": choices,
            },
        )

    async with AsyncOpenAI(
        api_key="test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        max_retries=0,
    ) as client:
        monkeypatch.setattr(write, "get_openai", lambda _: client)
        if mode == "ok":
            assert await write.generate_section(ctx, "{}") == section()
        else:
            with pytest.raises((ValueError, ValidationError)):
                await write.generate_section(ctx, "{}")


def test_upload_pipeline_reaches_export(client, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from iris.pipeline.stages import select, transcribe
    from tests.test_api import wait_for_status
    from tests.test_ingest import make_media

    calls = []

    async def parse(**kwargs):
        schema = kwargs["response_format"]
        calls.append(schema)
        if schema is select.Decisions:
            content = kwargs["messages"][1]["content"]
            ids = [json.loads(p["text"])["moment_id"] for p in content if p["type"] == "text"]
            result = select.Decisions(
                decisions=[
                    select.Decision(moment_id=mid, keep=True, caption="Colored slide", crop=None)
                    for mid in ids
                ]
            )
        else:
            payload = json.loads(kwargs["messages"][1]["content"])
            ids = [r["id"] for r in payload["records"] if r["kind"] == "visual"]
            result = section(ids)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=result, refusal=None))]
        )

    async def transcribe_audio(**kwargs):
        return {
            "language": "english",
            "segments": [{"start": 0, "end": 2, "text": "Ship the prototype. Alex will test it."}],
        }

    fake = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)),
        audio=SimpleNamespace(transcriptions=SimpleNamespace(create=transcribe_audio)),
    )
    for module in (write, select, transcribe):
        monkeypatch.setattr(module, "get_openai", lambda _: fake)
    recording = make_media(tmp_path / "talk.mp4", seconds=2)
    result = client.post("/api/sessions", files={"file": ("talk.mp4", recording.read_bytes())})
    assert result.status_code == 201
    sid = result.json()["id"]
    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "done", session.get("error")
    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert notes["visuals"]
    assert "Ship the prototype" in notes["markdown"]
    assert "[visual:" not in notes["markdown"]
    exported = client.get(f"/api/sessions/{sid}/export")
    assert exported.status_code == 200
    from markdown_it import MarkdownIt

    image_tokens = [
        child
        for token in MarkdownIt().parse(exported.text)
        for child in (token.children or [])
        if child.type == "image"
    ]
    assert image_tokens
    assert all(t.attrGet("src").startswith("data:image/jpeg;base64,") for t in image_tokens)
    assert calls == [select.Decisions, write.WrittenSection]
