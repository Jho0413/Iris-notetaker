"""The read-only MCP server (iris mcp): listing, reading and searching notes from the data
folder, through the MCP protocol as an assistant calls it."""

import os
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from iris import mcp_server
from iris.config import Settings
from iris.mcp_server import MAX_SEARCH_RESULTS, create_server
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

GENERATED = "Intro.\n\n# Forces\nGravity pulls down.\n\n::figure{#v1}"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", openai_api_key=None)


@pytest.fixture
def store(settings: Settings):
    store = SessionStore(settings.db_path)  # as the app writes it
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


# --- tools ----------------------------------------------------------------------------------


async def test_every_tool_is_read_only(settings, store):
    async with Client(create_server(settings)) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == {"list_sessions", "get_notes", "search"}
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False


# --- list_sessions ---------------------------------------------------------------------------


async def test_list_shows_both_titles_kind_and_whether_notes_can_be_read(settings, store):
    done = add_session(store, settings, "Physics")
    running = add_session(store, settings, status=SessionStatus.PROCESSING, markdown=None)
    sessions = await listed(settings)
    assert [s["id"] for s in sessions] == [running, done]  # newest first
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
    assert not settings.data_dir.exists()  # looking never creates it


# --- get_notes -------------------------------------------------------------------------------


async def test_notes_have_a_header_and_figures_as_captions(settings, store):
    sid = add_session(store, settings, "Physics")
    text = await text_of(settings, "get_notes", session_id=sid)
    header, body = text.split("\n\n", 2)[0:2], text.split("\n\n", 2)[2]
    assert header[0] == "# Loops and forces"
    assert header[1].startswith(f'Session "Physics" ({sid}) · lecture · added to Iris ')
    assert body == "Intro.\n\n# Forces\nGravity pulls down.\n\n[Figure: Forces on a loop]"
    assert "edit" not in text


async def test_notes_give_the_date_where_the_user_is(settings, store, monkeypatch):
    sid = add_session(store, settings)
    with sqlite3.connect(settings.db_path) as conn:  # added 03:00 UTC on 2 November
        conn.execute("UPDATE sessions SET created_at = '2026-11-02T03:00:00+00:00'")
    monkeypatch.setenv("TZ", "America/Los_Angeles")  # 20:00 on 1 November there
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
    assert text.endswith("My own notes.\n\n[Figure: Forces on a loop]")
    assert "Gravity" not in text


async def test_notes_made_again_after_the_edits_are_given_as_the_newest(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "My own notes, torque.", stale=True)
    text = await text_of(settings, "get_notes", session_id=sid)
    assert "Gravity pulls down." in text
    assert "My own notes" not in text and "edits" not in text
    assert [r["session_id"] for r in await search(settings, "gravity")] == [sid]
    assert await search(settings, "torque") == []
    assert session_paths(settings.sessions_dir, sid).notes_edits.exists()  # kept for the app


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
    path.mkdir()  # can't be read as a file, as with wrong permissions
    sessions = {s["id"]: s for s in await listed(settings)}
    assert sessions[good]["has_notes"] is True
    # Edits that can't be opened are never passed over for the generated notes: they may be
    # intact, and the user should see why rather than notes without their edits.
    assert sessions[bad]["has_notes"] is False
    assert {r["session_id"] for r in await search(settings, "gravity")} == {good}
    message = await error_of(settings, "get_notes", session_id=bad)
    assert "couldn't be opened" in message and "permissions" in message
    assert "Making them again" not in message  # a re-run would cost money and not help


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


# --- search ----------------------------------------------------------------------------------


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


# --- read only -------------------------------------------------------------------------------


def data_files(settings: Settings) -> dict[Path, int]:
    # Without SQLite's -wal and -shm files: it creates them to read a WAL database, even
    # read-only; they are its bookkeeping, not data.
    return {
        p: p.stat().st_mtime_ns
        for p in settings.data_dir.rglob("*")
        if not p.name.endswith(("-wal", "-shm"))
    }


async def test_reading_everything_changes_no_file(settings, store):
    sid = add_session(store, settings)
    save_edits(settings, sid, "My notes.")
    store.close()  # as when Iris is closed
    before = data_files(settings)
    await listed(settings)
    await text_of(settings, "get_notes", session_id=sid)
    await search(settings, "notes")
    assert data_files(settings) == before


# --- the command -----------------------------------------------------------------------------


async def test_iris_mcp_serves_over_stdio_and_ignores_the_folders_env_file(
    settings, store, tmp_path
):
    """The command an assistant runs: stdout must carry only the protocol. It starts in the
    assistant's chosen folder, whose .env (another project's) must not apply."""
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
