import base64
import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from mcp import Client, StdioServerParameters

from iris import mcp_server
from iris.config import Settings
from iris.mcp_server import (
    MAX_IMAGE_EDGE_PX,
    MAX_IMAGE_PIXELS,
    MAX_SEARCH_RESULTS,
    create_server,
    fitted_size,
)
from iris.models import (
    GENERIC_FAILURE,
    FailureKind,
    FrameRef,
    Notes,
    NotesEdits,
    NotesVisual,
    SessionStatus,
    SessionType,
    SourceKind,
)
from iris.notes_edits import generated_hash
from iris.notes_text import MAX_PASSAGES_PER_SESSION, MAX_QUERY_WORDS
from iris.store.db import SessionStore
from iris.store.layout import session_paths, write_model
from tests.media import image_bytes

GENERATED = "Intro.\n\n# Forces\nGravity pulls down.\n\n::figure{#v1}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", openai_api_key=None)


@pytest.fixture
def store(settings: Settings):
    store = SessionStore(settings.db_path)
    yield store
    store.close()


def add_session(
    store: SessionStore,
    settings: Settings,
    title: str = "Live session",
    *,
    status: SessionStatus = SessionStatus.DONE,
    markdown: str | None = GENERATED,
    notes_title: str = "Loops and forces",
) -> str:
    session = store.create(title, SourceKind.BROWSER_LIVE)
    store.update(session.id, status=status)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    if markdown is not None:
        notes = Notes(
            session_id=session.id,
            session_type=SessionType.LECTURE,
            title=notes_title,
            markdown=markdown,
            visuals=[
                NotesVisual(
                    id="v1",
                    moment_id="m1",
                    frame=FrameRef(t=0, path="frames/0.jpg"),
                    caption="Forces on a loop",
                    path="visuals/v1.svg",
                )
            ],
            format_version=2,
        )
        write_model(paths.notes, notes)
    return session.id


def save_edits(settings: Settings, sid: str, markdown: str, *, stale: bool = False) -> None:
    paths = session_paths(settings.sessions_dir, sid)
    generated = Notes.model_validate_json(paths.notes.read_text())
    based_on = "an older version" if stale else generated_hash(generated)
    edits = NotesEdits(markdown=markdown, based_on=based_on, saved_at=datetime.now(UTC))
    write_model(paths.notes_edits, edits)


async def call(settings: Settings, tool: str, **arguments):
    async with Client(create_server(settings)) as client:
        return await client.call_tool(tool, arguments)


async def text_of(settings: Settings, tool: str, **arguments) -> str:
    result = await call(settings, tool, **arguments)
    assert not result.is_error, result.content
    return result.content[0].text


async def error_of(settings: Settings, tool: str, **arguments) -> str:
    result = await call(settings, tool, **arguments)
    assert result.is_error
    return result.content[0].text


async def listed(settings: Settings) -> list[dict]:
    return (await call(settings, "list_sessions")).structured_content["result"]


async def search(settings: Settings, query: str) -> list[dict]:
    result = await call(settings, "search", query=query)
    assert not result.is_error, result.content
    return result.structured_content["result"]


async def test_every_tool_is_read_only(settings, store):
    async with Client(create_server(settings)) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {"list_sessions", "get_notes", "get_figure", "search"}
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


async def test_list_shows_both_titles_kind_and_whether_notes_can_be_read(settings, store):
    done = add_session(store, settings, "Physics")
    running = add_session(store, settings, status=SessionStatus.PROCESSING, markdown=None)
    sessions = await listed(settings)
    assert [s["id"] for s in sessions] == [running, done]
    assert sessions[1] | {"added_at": None} == {
        "id": done,
        "title": "Physics",
        "notes_title": "Loops and forces",
        "added_at": None,
        "kind": "lecture",
        "status": "done",
        "has_notes": True,
        "duration_s": None,
    }
    assert sessions[0]["has_notes"] is False
    assert sessions[0]["kind"] is None and sessions[0]["notes_title"] is None
    assert sessions[0]["status"] == "processing"


async def test_damaged_notes_are_listed_as_unreadable(settings, store):
    sid = add_session(store, settings)
    session_paths(settings.sessions_dir, sid).notes.write_text("{not json")
    [session] = await listed(settings)
    assert session["has_notes"] is False


async def test_no_data_folder_says_where_it_looked(settings):
    message = await error_of(settings, "list_sessions")
    assert str(settings.data_dir.resolve()) in message
    assert "IRIS_DATA_DIR" in message
    assert not settings.data_dir.exists()


async def test_notes_have_a_header_and_figures_as_captions(settings, store):
    sid = add_session(store, settings, "Physics")
    text = await text_of(settings, "get_notes", session_id=sid)
    header, body = text.split("\n\n", 2)[0:2], text.split("\n\n", 2)[2]
    assert header[0] == "# Loops and forces"
    assert header[1].startswith(f'Session "Physics" ({sid}) · lecture · added to Iris ')
    assert body == "Intro.\n\n# Forces\nGravity pulls down.\n\n[Figure v1: Forces on a loop]"
    assert "edit" not in text


async def test_notes_give_the_date_where_the_user_is(settings, store, monkeypatch):
    sid = add_session(store, settings)
    with sqlite3.connect(settings.db_path) as conn:
        conn.execute("UPDATE sessions SET created_at = '2026-11-02T03:00:00+00:00'")
    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        text = await text_of(settings, "get_notes", session_id=sid)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert "added to Iris 2026-11-01" in text


async def test_notes_are_the_users_edited_version(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "My own notes.\n\n::figure{#v1}")
    text = await text_of(settings, "get_notes", session_id=sid)
    assert "These notes include the user's edits." in text
    assert text.endswith("My own notes.\n\n[Figure v1: Forces on a loop]")
    assert "Gravity" not in text


async def test_notes_made_again_after_the_edits_are_given_as_the_newest(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "My own notes, torque.", stale=True)
    text = await text_of(settings, "get_notes", session_id=sid)
    assert "Gravity pulls down." in text
    assert "My own notes" not in text and "edits" not in text
    assert [r["session_id"] for r in await search(settings, "gravity")] == [sid]
    assert await search(settings, "torque") == []
    assert session_paths(settings.sessions_dir, sid).notes_edits.exists()


async def test_damaged_edits_fall_back_to_the_generated_notes(settings, store):
    sid = add_session(store, settings)
    session_paths(settings.sessions_dir, sid).notes_edits.write_text("{not json")
    text = await text_of(settings, "get_notes", session_id=sid)
    assert "Gravity pulls down." in text and "edits" not in text


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (SessionStatus.CREATED, "aren't ready yet (status: created)"),
        (SessionStatus.PROCESSING, "aren't ready yet (status: processing)"),
        (SessionStatus.RECORDING, "aren't ready yet (status: recording)"),
        (SessionStatus.STOPPED, "stopped before notes were made"),
        (SessionStatus.DONE, "notes are missing"),
    ],
)
async def test_a_session_without_notes_says_why(settings, store, status, expected):
    sid = add_session(store, settings, status=status, markdown=None)
    assert expected in await error_of(settings, "get_notes", session_id=sid)


async def test_a_failed_session_gives_the_users_failure_message(settings, store):
    sid = add_session(store, settings, markdown=None)
    store.fail(sid, message=GENERIC_FAILURE, kind=FailureKind.GENERAL, detail="raw traceback")
    message = await error_of(settings, "get_notes", session_id=sid)
    assert message.endswith(f"Iris couldn't make notes for this session. {GENERIC_FAILURE}")
    assert "raw traceback" not in message


async def test_notes_kept_while_iris_makes_them_again_are_still_readable(settings, store):
    sid = add_session(store, settings, status=SessionStatus.PROCESSING)
    assert "Gravity pulls down." in await text_of(settings, "get_notes", session_id=sid)


@pytest.mark.parametrize("file", ["notes", "notes_edits"])
async def test_a_file_that_cant_be_opened_never_breaks_the_other_sessions(settings, store, file):
    good = add_session(store, settings, "Good")
    bad = add_session(store, settings, "Bad")
    save_edits(settings, bad, "# Mine\nGravity edited.")
    path = getattr(session_paths(settings.sessions_dir, bad), file)
    path.unlink()
    path.mkdir()
    sessions = {s["id"]: s for s in await listed(settings)}
    assert sessions[good]["has_notes"] is True
    assert sessions[bad]["has_notes"] is False
    assert {r["session_id"] for r in await search(settings, "gravity")} == {good}
    message = await error_of(settings, "get_notes", session_id=bad)
    assert "couldn't be opened" in message and "permissions" in message
    assert "Making them again" not in message


async def test_damaged_notes_say_so(settings, store):
    sid = add_session(store, settings)
    session_paths(settings.sessions_dir, sid).notes.write_text("{not json")
    message = await error_of(settings, "get_notes", session_id=sid)
    assert "damaged" in message and "Making them again in Iris" in message


@pytest.mark.parametrize("bad_id", ["nope", "../../etc/passwd", ""])
async def test_an_unknown_id_is_refused_without_touching_files(settings, store, bad_id):
    add_session(store, settings)
    message = await error_of(settings, "get_notes", session_id=bad_id)
    assert "There is no session" in message and "list_sessions" in message


SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20">'
    '<rect width="40" height="20" fill="#4b44d6"/></svg>'
)


def set_figure(settings: Settings, sid: str, path: str, data: bytes | None) -> None:
    paths = session_paths(settings.sessions_dir, sid)
    notes = Notes.model_validate_json(paths.notes.read_text())
    notes.visuals[0].path = path
    if path.endswith(".svg"):
        notes.visuals[0].render_as, notes.visuals[0].verified = "svg", True
    write_model(paths.notes, notes)
    if data is not None:
        (paths.root / path).write_bytes(data)


async def figure(settings: Settings, sid: str, figure_id: str = "v1"):
    return await call(settings, "get_figure", session_id=sid, figure_id=figure_id)


def encoded(width: int, height: int, ext: str) -> bytes:
    return image_bytes(np.full((height, width, 3), 120, np.uint8), ext)


def size_of(image) -> tuple[int, int]:
    decoded = cv2.imdecode(np.frombuffer(base64.b64decode(image.data), np.uint8), cv2.IMREAD_COLOR)
    return decoded.shape[1], decoded.shape[0]


async def test_a_redrawn_figure_comes_as_a_png_after_its_caption(settings, store):
    sid = add_session(store, settings)
    set_figure(settings, sid, "visuals/v1.svg", SVG.encode())
    result = await figure(settings, sid)
    assert not result.is_error, result.content
    caption, origin, image = result.content
    assert caption.text == "[Figure v1: Forces on a loop]"
    assert origin.text == "Iris's redraw, checked against the screen."
    assert image.type == "image" and image.mime_type == "image/png"
    width, height = size_of(image)
    assert width <= MAX_IMAGE_EDGE_PX and width * height <= MAX_IMAGE_PIXELS
    assert width > MAX_IMAGE_EDGE_PX * 0.8


@pytest.mark.parametrize(
    ("name", "mime"),
    [("v1.jpg", "image/jpeg"), ("v1.png", "image/png"), ("v1.PNG", "image/png")],
)
async def test_an_image_that_fits_is_sent_unchanged(settings, store, name, mime):
    sid = add_session(store, settings)
    data = encoded(400, 300, ".png" if mime == "image/png" else ".jpg")
    set_figure(settings, sid, f"visuals/{name}", data)
    [_, _, image] = (await figure(settings, sid)).content
    assert (image.mime_type, base64.b64decode(image.data)) == (mime, data)


@pytest.mark.parametrize("ext", [".png", ".jpg"])
async def test_a_large_image_is_scaled_down_to_claudes_limits(settings, store, ext):
    sid = add_session(store, settings)
    set_figure(settings, sid, f"visuals/v1{ext}", encoded(3000, 2000, ext))
    [_, _, image] = (await figure(settings, sid)).content
    width, height = size_of(image)
    assert width <= MAX_IMAGE_EDGE_PX and width * height <= MAX_IMAGE_PIXELS
    assert abs(width / height - 1.5) < 0.01


async def test_an_image_still_too_large_to_send_is_refused(settings, store, monkeypatch):
    sid = add_session(store, settings)
    set_figure(settings, sid, "visuals/v1.png", encoded(400, 300, ".png"))
    monkeypatch.setattr(mcp_server, "MAX_IMAGE_BYTES", 10)
    message = await error_of(settings, "get_figure", session_id=sid, figure_id="v1")
    assert "too large to send" in message


@pytest.mark.parametrize(
    ("width", "height", "fitted"),
    [
        (1568, 700, None),
        (3136, 1000, (1568, 500)),
        (1500, 1500, (1072, 1072)),
        (100, 20000, (7, 1568)),
    ],
)
def test_fitted_size(width, height, fitted):
    assert fitted_size(width, height) == fitted


@pytest.mark.parametrize(
    ("path", "data", "expected"),
    [
        ("visuals/v1.svg", None, "missing from the session's folder"),
        ("../outside.png", b"secret", "can't be shown"),
        ("visuals/v1.gif.exe", b"x", "can't be shown"),
        ("visuals/v1.svg", b"<not svg", "couldn't be drawn"),
        ("visuals/v1.png", b"not an image", "couldn't be read"),
    ],
)
async def test_a_figure_image_that_cant_be_sent_says_why(settings, store, path, data, expected):
    sid = add_session(store, settings)
    if path.startswith(".."):
        (settings.sessions_dir / "outside.png").write_bytes(data)
        data = None
    set_figure(settings, sid, path, data)
    assert expected in await error_of(settings, "get_figure", session_id=sid, figure_id="v1")


async def test_a_figure_image_linked_from_outside_the_session_is_refused(settings, store):
    sid = add_session(store, settings)
    outside = settings.sessions_dir / "outside.png"
    outside.write_bytes(encoded(40, 20, ".png"))
    set_figure(settings, sid, "visuals/v1.png", None)
    (session_paths(settings.sessions_dir, sid).visuals_dir / "v1.png").symlink_to(outside)
    assert "can't be shown" in await error_of(
        settings, "get_figure", session_id=sid, figure_id="v1"
    )


async def test_a_figure_image_that_cant_be_opened_says_so(settings, store):
    sid = add_session(store, settings)
    set_figure(settings, sid, "visuals/v1.png", None)
    (session_paths(settings.sessions_dir, sid).visuals_dir / "v1.png").mkdir()
    assert "couldn't be opened" in await error_of(
        settings, "get_figure", session_id=sid, figure_id="v1"
    )


async def test_an_unknown_figure_or_session_is_refused(settings, store):
    sid = add_session(store, settings)
    assert "no figure 'v9'" in await error_of(
        settings, "get_figure", session_id=sid, figure_id="v9"
    )
    assert "There is no session" in await error_of(
        settings, "get_figure", session_id="nope", figure_id="v1"
    )


async def test_a_figure_of_a_session_without_notes_says_why(settings, store):
    sid = add_session(store, settings, status=SessionStatus.PROCESSING, markdown=None)
    message = await error_of(settings, "get_figure", session_id=sid, figure_id="v1")
    assert "aren't ready yet" in message


async def test_search_finds_passages_across_sessions_best_first(settings, store):
    one = add_session(store, settings, markdown="# Forces\nGravity, gravity and mass.")
    two = add_session(store, settings, markdown="# Orbits\nGravity holds the moon.")
    add_session(store, settings, markdown="# Other\nNothing here.")
    results = await search(settings, "GRAVITY")
    assert [(r["session_id"], r["heading"]) for r in results] == [(one, "Forces"), (two, "Orbits")]
    assert results[0]["snippet"] == "# Forces Gravity, gravity and mass."
    assert results[0]["notes_title"] == "Loops and forces"


async def test_search_matches_the_titles_and_figure_captions(settings, store):
    sid = add_session(store, settings, "Standup", markdown="# Pricing\nKeep it.\n\n::figure{#v1}")
    assert [r["session_id"] for r in await search(settings, "standup pricing")] == [sid]
    assert [r["session_id"] for r in await search(settings, "loop forces")] == [sid]


async def test_search_needs_every_word_in_one_passage(settings, store):
    add_session(store, settings, markdown="# A\nGravity.\n\n# B\nMoon.")
    assert await search(settings, "gravity moon") == []


async def test_search_returns_at_most_the_limit(settings, store):
    for _ in range(MAX_SEARCH_RESULTS + 2):
        add_session(store, settings, markdown="# Forces\nGravity.")
    assert len(await search(settings, "gravity")) == MAX_SEARCH_RESULTS


async def test_equal_matches_come_newest_first(settings, store):
    older = add_session(store, settings, markdown="# Forces\nGravity.")
    newer = add_session(store, settings, markdown="# Forces\nGravity.")
    assert [r["session_id"] for r in await search(settings, "gravity")] == [newer, older]


async def test_one_session_gives_only_a_few_passages(settings, store):
    many = "\n\n".join(f"# Part {i}\nGravity." for i in range(MAX_PASSAGES_PER_SESSION + 2))
    sid = add_session(store, settings, markdown=many)
    other = add_session(store, settings, markdown="# Orbits\nGravity.")
    results = await search(settings, "gravity")
    assert [r["session_id"] for r in results].count(sid) == MAX_PASSAGES_PER_SESSION
    assert other in {r["session_id"] for r in results}


async def test_search_skips_sessions_without_readable_notes(settings, store):
    good = add_session(store, settings)
    add_session(store, settings, status=SessionStatus.PROCESSING, markdown=None)
    damaged = add_session(store, settings)
    session_paths(settings.sessions_dir, damaged).notes.write_text("{not json")
    assert [r["session_id"] for r in await search(settings, "gravity")] == [good]


async def test_search_uses_the_users_edits(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "# Mine\nTorque.")
    assert [r["session_id"] for r in await search(settings, "torque")] == [sid]
    assert await search(settings, "gravity pulls") == []


async def test_edited_notes_with_odd_line_breaks_are_searched(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "Intro\r\r\r# Heading\nbody gravity")
    [result] = await search(settings, "gravity")
    assert (result["session_id"], result["heading"]) == (sid, "Heading")


async def test_one_session_search_trips_on_never_stops_the_others(settings, store, monkeypatch):
    good = add_session(store, settings, "Good")
    add_session(store, settings, "Bad")
    real = mcp_server.find_passages

    def trips_on_bad(titles, text, words):
        if titles.startswith("Bad"):
            raise IndexError("a reader bug")
        return real(titles, text, words)

    monkeypatch.setattr(mcp_server, "find_passages", trips_on_bad)
    assert [r["session_id"] for r in await search(settings, "gravity")] == [good]


async def test_a_damaged_store_says_where_it_looked(settings):
    settings.data_dir.mkdir(parents=True)
    settings.db_path.write_text("not a database")
    message = await error_of(settings, "list_sessions")
    assert "couldn't be read" in message and str(settings.data_dir.resolve()) in message


async def test_a_search_without_words_is_refused(settings, store):
    assert "at least one word" in await error_of(settings, "search", query=" ?! ")


async def test_a_search_with_too_many_words_is_refused(settings, store):
    add_session(store, settings)
    words = " ".join(f"word{i}" for i in range(MAX_QUERY_WORDS + 1))
    assert f"at most {MAX_QUERY_WORDS} words" in await error_of(settings, "search", query=words)
    allowed = " ".join(f"word{i}" for i in range(MAX_QUERY_WORDS))
    assert await search(settings, allowed) == []


def data_files(settings: Settings) -> dict[Path, int]:
    return {
        p: p.stat().st_mtime_ns
        for p in settings.data_dir.rglob("*")
        if not p.name.endswith(("-wal", "-shm"))
    }


async def test_reading_everything_changes_no_file(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "My notes.")
    store.close()
    before = data_files(settings)
    await listed(settings)
    await text_of(settings, "get_notes", session_id=sid)
    await search(settings, "notes")
    assert data_files(settings) == before


async def test_iris_mcp_serves_over_stdio_and_ignores_the_folders_env_file(
    settings, store, tmp_path
):
    sid = add_session(store, settings, "Physics")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("IRIS_DATA_DIR=/elsewhere\nIRIS_RENDER_ATTEMPTS=not-a-number\n")
    command = StdioServerParameters(
        command=sys.executable,
        args=["-m", "iris", "mcp"],
        env={**os.environ, "IRIS_DATA_DIR": str(settings.data_dir)},
        cwd=project,
    )
    async with Client(command) as client:
        result = await client.call_tool("list_sessions", {})
    assert [s["id"] for s in result.structured_content["result"]] == [sid]
