import time
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.models import Notes, SessionType, SourceKind
from iris.store.layout import session_paths, write_model


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

    # The upload is not a real video, so the pipeline should fail cleanly at ingest.
    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "failed"
    assert session["current_stage"] == "ingest"
    assert session["error"] == (
        "ingest failed: ValueError: source.mp4 is not a readable audio or video file"
    )

    assert [s["id"] for s in client.get("/api/sessions").json()] == [sid]
    assert client.get(f"/api/sessions/{sid}/notes").status_code == 404


def test_live_session_accepts_chunks(client: TestClient):
    sid = client.post("/api/live", json={"title": "standup"}).json()["id"]
    assert client.post(f"/api/live/{sid}/audio?seq=0", content=b"webm").status_code == 204
    assert client.post(f"/api/live/{sid}/frame?t=1.5", content=b"jpeg").status_code == 204
    assert client.post(f"/api/live/{sid}/end").status_code == 200
    wait_for_status(client, sid, {"done", "failed"})
    # No more input once recording has ended
    assert client.post(f"/api/live/{sid}/audio?seq=1", content=b"x").status_code == 409


def test_file_serving_rejects_other_dirs(client: TestClient):
    sid = client.post("/api/live", json={}).json()["id"]
    assert client.get(f"/api/sessions/{sid}/files/live/x.webm").status_code == 404


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
        ("会议记录", None),  # nothing ASCII left: falls back to the session id
        ("Demo 🎉 day", "Demo day.md"),
        ('Say "hi"', "Say _hi_.md"),
        ("a/b\\c", "a_b_c.md"),
        ("line\nbreak\tend", "line break end.md"),
        ("Q1／Q2 review", "Q1_Q2 review.md"),  # full-width slash folds to "/" under NFKD
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
    header.encode("ascii")  # header values must be pure ASCII
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
