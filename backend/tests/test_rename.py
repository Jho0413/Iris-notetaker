"""Renaming a session (PATCH /api/sessions/{id})."""

from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from iris.api.routes.sessions import TITLE_MAX_CHARS
from iris.config import Settings
from iris.main import create_app
from iris.models import SessionStatus

from .test_api import make_session_with_notes, wait_for_status


def rename(client: TestClient, sid: str, title: object):
    return client.patch(f"/api/sessions/{sid}", json={"title": title})


def new_session(client: TestClient, title: str = "Live session") -> str:
    return client.post("/api/live", json={"title": title}).json()["id"]


def test_rename_trims_and_shows_everywhere(client: TestClient):
    sid = new_session(client)
    before = client.get(f"/api/sessions/{sid}").json()["updated_at"]
    r = rename(client, sid, "  Design review  ")
    assert r.status_code == 200
    assert r.json()["title"] == "Design review"
    assert r.json()["updated_at"] > before  # the reply is the session as stored now
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Design review"
    assert [s["title"] for s in client.get("/api/sessions").json()] == ["Design review"]


@pytest.mark.parametrize("title", ["", "   ", "\n\t", "x" * (TITLE_MAX_CHARS + 1), None, 7])
def test_rename_refuses_empty_too_long_or_not_text(client: TestClient, title: object):
    sid = new_session(client, "Kept")
    assert rename(client, sid, title).status_code == 422
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Kept"


def test_rename_takes_exactly_the_longest_name(client: TestClient):
    sid = new_session(client)
    title = "y" * TITLE_MAX_CHARS
    assert rename(client, sid, title).json()["title"] == title


def test_rename_refuses_half_an_emoji(client: TestClient):
    sid = new_session(client, "Kept")
    r = client.patch(
        f"/api/sessions/{sid}",
        content=b'{"title": "ab\\ud83c"}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422  # not a 500: the echoed input is escaped (iris/main.py)
    assert r.json()["detail"][0]["loc"] == ["body", "title"]
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Kept"


def test_rename_refuses_other_fields(client: TestClient):
    sid = new_session(client)
    assert (
        client.patch(f"/api/sessions/{sid}", json={"title": "A", "status": "done"}).status_code
        == 422
    )


def test_rename_unknown_session(client: TestClient):
    assert rename(client, "nope", "Anything").status_code == 404


@pytest.mark.parametrize(
    "title", ["Cours d’algèbre · semaine 7", "第七講 線形代数", "Demo day 🎉🚀", "Ünïcødé ‼"]
)
def test_rename_keeps_non_ascii_and_names_the_export(
    client: TestClient, settings: Settings, title: str
):
    sid = make_session_with_notes(client, settings, "Old name")
    assert rename(client, sid, title).json()["title"] == title
    header = client.get(f"/api/sessions/{sid}/export").headers["content-disposition"]
    header.encode("ascii")  # header values stay pure ASCII
    assert unquote(header.split("filename*=UTF-8''")[1], errors="strict") == f"{title}.md"


def test_rename_while_recording_and_after_a_run(client: TestClient):
    sid = new_session(client)
    assert client.get(f"/api/sessions/{sid}").json()["status"] == SessionStatus.RECORDING
    assert rename(client, sid, "Renamed while recording").status_code == 200
    # Stopping runs the pipeline (it fails at ingest here: no real input), which keeps the name.
    assert client.post(f"/api/live/{sid}/end").status_code == 200
    wait_for_status(client, sid, {"done", "failed"})
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Renamed while recording"
    # A re-run keeps it too.
    client.post(f"/api/sessions/{sid}/run")
    wait_for_status(client, sid, {"done", "failed"})
    assert client.get(f"/api/sessions/{sid}").json()["title"] == "Renamed while recording"


def test_rename_survives_an_engine_restart(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", openai_api_key=None)
    with TestClient(create_app(settings)) as client:
        sid = new_session(client)
        rename(client, sid, "Kept across restarts")
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/sessions/{sid}").json()["title"] == "Kept across restarts"
