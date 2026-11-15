import json
from unittest.mock import AsyncMock

import pytest

from iris.llm.errors import UserFacingError
from iris.models import (
    NOTHING_TO_WRITE,
    EventType,
    FrameRef,
    Notes,
    NotesDraft,
    Selection,
    SessionType,
    Transcript,
    TranscriptSegment,
    Visual,
)
from iris.pipeline.stages import assemble, write
from iris.store.layout import read_model, write_model
from tests.builders import markdown_images, section, stage_ctx
from tests.fakes import PipelineOpenAI, install_get_openai
from tests.media import make_media
from tests.waiting import wait_for_status


@pytest.fixture
def setup(tmp_path, monkeypatch):
    events = []
    ctx = stage_ctx(tmp_path, events=events)
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
    assert "::figure{#v1}" in notes.markdown
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
    cache = next(ctx.paths.notes_sections_dir.glob("*.json"))
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
        section(["v1"], ":::warning[Careful]{color=red}\nText.\n:::"),
        section(["v1"], ":::box[A]\n:::box[B]\n:::box[C]\nToo deep.\n:::\n:::\n:::"),
        section(["v1"], "Text.\n\n::figure{#v1}"),
        section(["v1"], "Text.\n\n::chart{#v1}"),
        write.WrittenSection(session_type=SessionType.DEMO, title="", blocks=[]),
    ],
)
async def test_invalid_output_is_not_cached_or_published(setup, bad):
    ctx, _, _, generate, events = setup
    generate.return_value = bad
    with pytest.raises(ValueError):
        await write.run(ctx)
    assert not ctx.paths.notes_draft.exists()
    assert not list(ctx.paths.notes_sections_dir.glob("*.json"))
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
    with pytest.raises(UserFacingError, match="no transcript") as raised:
        await write.run(ctx)
    assert raised.value.message == NOTHING_TO_WRITE
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

    ctx = stage_ctx(tmp_path)

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
    from iris.pipeline.stages import render, select, transcribe

    fake = PipelineOpenAI()
    install_get_openai(monkeypatch, fake, write, select, transcribe, render)
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
    image_tokens = markdown_images(exported.text)
    assert image_tokens, exported.text[:600]
    assert all(t.attrGet("src").startswith("data:image/png;base64,") for t in image_tokens)
    schemas = [c["response_format"] for c in fake.calls]
    assert schemas[0] is select.Decisions
    assert sorted(c.__name__ for c in schemas[1:]) == ["Check", "SvgDrawing", "WrittenSection"]


async def test_screen_content_is_evidence_and_visuals_are_placed_once(setup):
    from iris.models import BoardElement, MomentContent

    ctx, _, visual, generate, _ = setup
    visual.description = "Mockup of the prototype screen"
    contents = [
        MomentContent(
            moment_id="m1",
            start=0,
            end=10,
            elements=[
                BoardElement(kind="math", content="3(x+3)^2 - 28", t=4),
                BoardElement(kind="figure", content="Mockup", visual_id="v1"),
            ],
        )
    ]
    write_model(ctx.paths.selection, Selection(visuals=[visual], contents=contents))
    await write.run(ctx)
    records = json.loads(generate.call_args.args[1])["records"]
    assert [r["kind"] for r in records] == ["speech", "visual", "screen"]
    assert records[1]["description"] == "Mockup of the prototype screen"
    assert records[2] == {"kind": "screen", "start": 4, "type": "math", "content": "3(x+3)^2 - 28"}
    twice = write.WrittenSection(
        session_type=SessionType.MEETING,
        title="Prototype review",
        blocks=[
            write.NoteBlock(markdown="Intro $x_1 < y$.", visual_ids=["v1"]),
            write.NoteBlock(markdown="More.", visual_ids=["v1"]),
        ],
    )
    with pytest.raises(ValueError, match="exactly once"):
        write.validate_section(twice, write.plan_parts(*_inputs(ctx), 24_000)[0], None)


def _inputs(ctx):
    return read_model(ctx.paths.transcript, Transcript), read_model(ctx.paths.selection, Selection)


async def test_fixable_format_slips_are_repaired_not_failed(setup):
    ctx, _, _, generate, _ = setup
    generate.return_value = section(
        ["v1"], ":::\n:::box[Decision]{color=purple}\nShip the prototype. Alex will test it."
    )
    await write.run(ctx)
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.markdown == (
        ":::box[Decision]{color=grey}\nShip the prototype. Alex will test it.\n\n[visual:v1]\n\n:::"
    )
    await write.run(ctx)
    assert generate.await_count == 1


def test_a_steps_figures_go_inside_the_step_at_its_end():
    step = ":::step[Find the forces]{color=indigo}\nAt the bottom.\n:::box[Key idea]\nP.\n:::\n:::"
    written = write.WrittenSection(
        session_type=SessionType.LECTURE,
        title="Loops",
        blocks=[
            write.NoteBlock(markdown="Intro.", visual_ids=[]),
            write.NoteBlock(markdown=step, visual_ids=["v1", "v2"]),
            write.NoteBlock(
                markdown=":::box[Answer]{color=green}\nHeavier.\n:::\n\nAfter.", visual_ids=["v3"]
            ),
        ],
    )
    assert write.section_markdown(written) == (
        "Intro.\n\n"
        ":::step[Find the forces]{color=indigo}\nAt the bottom.\n:::box[Key idea]\nP.\n:::"
        "\n\n[visual:v1]\n\n[visual:v2]\n\n:::\n\n"
        ":::box[Answer]{color=green}\nHeavier.\n:::\n\nAfter.\n\n[visual:v3]"
    )


async def test_a_title_with_brackets_in_pairs_is_kept(setup):
    ctx, _, _, generate, _ = setup
    generate.return_value = section(
        ["v1"],
        ":::box[Closed interval [a, b]]{color=blue}\nShip the prototype. Alex will test it.\n:::",
    )
    await write.run(ctx)
    draft = read_model(ctx.paths.notes_draft, NotesDraft)
    assert draft.markdown.startswith(":::box[Closed interval [a, b]]{color=blue}")
