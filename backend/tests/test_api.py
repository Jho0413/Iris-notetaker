import time
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.models import UNREADABLE_UPLOAD, Notes, SessionType, SourceKind
from iris.store.layout import read_model, session_paths, write_model


def wait_for_status(client: TestClient, sid: str, statuses: set[str], timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get(f"/api/sessions/{sid}").json()
        if session["status"] in statuses:
            return session
        time.sleep(0.05)
    raise AssertionError(f"session {sid} never reached {statuses}")


def test_health(client: TestClient):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_upload_creates_session_and_runs_pipeline(client: TestClient):
    r = client.post("/api/sessions", files={"file": ("talk.mp4", b"not really a video")})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["title"] == "talk"

    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "failed"
    assert session["current_stage"] == "ingest"
    assert session["failure_kind"] == "input"
    assert session["failure_message"] == UNREADABLE_UPLOAD
    assert "error" not in session and "error_detail" not in session
    detail = client.app.state.store.failure_detail(sid)
    assert detail.error.startswith("ingest failed: UserFacingError: source.mp4 is not a readable")

    assert [s["id"] for s in client.get("/api/sessions").json()] == [sid]
    assert client.get(f"/api/sessions/{sid}/notes").status_code == 404


def test_live_session_accepts_chunks(client: TestClient):
    sid = client.post("/api/live", json={"title": "standup"}).json()["id"]
    webm, jpeg = b"\x1a\x45\xdf\xa3webm", b"\xff\xd8\xffjpeg"
    assert client.post(f"/api/live/{sid}/audio?seq=0", content=webm).status_code == 204
    assert client.post(f"/api/live/{sid}/frame?t=1.5&share=1", content=jpeg).status_code == 204
    assert client.post(f"/api/live/{sid}/end").status_code == 200
    wait_for_status(client, sid, {"done", "failed"})
    assert client.post(f"/api/live/{sid}/audio?seq=1", content=b"x").status_code == 409


def test_file_serving_rejects_other_dirs(client: TestClient):
    sid = client.post("/api/live", json={}).json()["id"]
    assert client.get(f"/api/sessions/{sid}/files/live/x.webm").status_code == 404


def test_a_transcript_not_made_yet_is_not_ready(client: TestClient, settings: Settings):
    sid = client.app.state.store.create("Loops", SourceKind.UPLOAD).id
    session_paths(settings.sessions_dir, sid).ensure()
    r = client.get(f"/api/sessions/{sid}/transcript")
    assert r.status_code == 404
    assert r.json()["detail"] == "transcript not ready"


def test_a_damaged_transcript_is_refused_in_plain_words_and_logged(
    client: TestClient, settings: Settings, caplog
):
    sid = client.app.state.store.create("Loops", SourceKind.UPLOAD).id
    session_paths(settings.sessions_dir, sid).ensure().transcript.write_text("{not json")
    r = client.get(f"/api/sessions/{sid}/transcript")
    assert r.status_code == 500
    assert r.json()["detail"] == "the transcript couldn't be read"
    assert f"can't read the transcript of session {sid}" in caplog.text


def make_session_with_notes(client: TestClient, settings: Settings, title: str) -> str:
    session = client.app.state.store.create(title, SourceKind.UPLOAD)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    notes = Notes(
        session_id=session.id,
        session_type=SessionType.MEETING,
        title=title,
        markdown="Body text.",
        visuals=[],
    )
    write_model(paths.notes, notes)
    return session.id


@pytest.mark.parametrize(
    ("title", "ascii_name"),
    [
        ("Plain title", "Plain title.md"),
        ("Café résumé", "Cafe resume.md"),
        ("会议记录", None),
        ("Demo 🎉 day", "Demo day.md"),
        ('Say "hi"', "Say _hi_.md"),
        ("a/b\\c", "a_b_c.md"),
        ("line\nbreak\tend", "line break end.md"),
        ("Q1／Q2 review", "Q1_Q2 review.md"),
        ("a＼b ＂c＂", "a_b _c_.md"),
    ],
)
def test_export_filename_keeps_title(
    client: TestClient, settings: Settings, title: str, ascii_name: str | None
):
    sid = make_session_with_notes(client, settings, title)
    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "Body text." in r.text

    header = r.headers["content-disposition"]
    header.encode("ascii")
    disposition, plain, extended = (part.strip() for part in header.split(";"))
    assert disposition == "attachment"
    assert plain == f'filename="{ascii_name or sid + ".md"}"'
    prefix = "filename*=UTF-8''"
    assert extended.startswith(prefix)
    encoded = extended.removeprefix(prefix)
    assert not set(encoded) & set(' "\\;,')
    expected = " ".join(title.replace("/", "_").replace("\\", "_").split()) + ".md"
    assert unquote(encoded, encoding="utf-8", errors="strict") == expected


def test_export_filename_falls_back_to_session_id_for_blank_title(
    client: TestClient, settings: Settings
):
    sid = make_session_with_notes(client, settings, "   ")
    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        f"attachment; filename=\"{sid}.md\"; filename*=UTF-8''{sid}.md"
    )


@pytest.mark.parametrize("title", ["\u200b", "\u200d\u2060", "\u202e", "\u200b \u200b"])
def test_export_filename_treats_invisible_title_as_blank(
    client: TestClient, settings: Settings, title: str
):
    sid = make_session_with_notes(client, settings, title)
    r = client.get(f"/api/sessions/{sid}/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == (
        f"attachment; filename=\"{sid}.md\"; filename*=UTF-8''{sid}.md"
    )


def test_export_filename_drops_bidi_controls_but_keeps_emoji_sequences(
    client: TestClient, settings: Settings
):
    sid = make_session_with_notes(client, settings, "\u202egpj.exe 👨\u200d👩\u200d👧")
    header = client.get(f"/api/sessions/{sid}/export").headers["content-disposition"]
    encoded = header.split("filename*=UTF-8''")[1]
    assert unquote(encoded) == "gpj.exe 👨\u200d👩\u200d👧.md"
    assert 'filename="gpj.exe.md"' in header


def test_old_notes_are_upgraded_on_read_and_left_as_they_are_on_disk(client, settings):
    sid = make_session_with_notes(client, settings, "Loops")
    paths = session_paths(settings.sessions_dir, sid)
    old = read_model(paths.notes, Notes).model_copy(
        update={"markdown": "**Answer:** It works.", "format_version": 1}
    )
    write_model(paths.notes, old)
    on_disk = paths.notes.read_text()

    notes = client.get(f"/api/sessions/{sid}/notes").json()
    assert notes["markdown"] == ":::box[Answer]{color=green}\nIt works.\n:::"
    assert notes["format_version"] == 2
    assert paths.notes.read_text() == on_disk
