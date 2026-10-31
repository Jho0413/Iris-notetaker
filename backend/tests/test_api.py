import time

from fastapi.testclient import TestClient


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

    # Stages are still stubs, so the pipeline should fail cleanly at ingest.
    session = wait_for_status(client, sid, {"done", "failed"})
    assert session["status"] == "failed"
    assert session["current_stage"] == "ingest"
    assert session["error"] == "ingest failed: NotImplementedError: upload ingest"

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
