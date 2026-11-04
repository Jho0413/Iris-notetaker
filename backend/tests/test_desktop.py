"""Backend support for the desktop app: ``python -m iris``, the per-launch API token
(``IRIS_API_TOKEN``) and the parent watcher (``IRIS_PARENT_PID``)."""

import asyncio
import runpy
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import iris.parent
from iris.config import Settings
from iris.main import create_app
from iris.parent import pid_alive, watch_parent

TOKEN = "s3cret-token-0123456789abcdef"


def _client(tmp_path: Path, **settings) -> TestClient:
    return TestClient(create_app(Settings(data_dir=tmp_path / "data", **settings)))


# --- python -m iris ---------------------------------------------------------------------------


def test_python_m_iris_runs_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["iris", "--help"])
    with pytest.raises(SystemExit) as exit_:
        runpy.run_module("iris", run_name="__main__")
    assert exit_.value.code == 0
    assert "serve" in capsys.readouterr().out


# --- API token --------------------------------------------------------------------------------


def test_no_token_configured_leaves_the_api_open(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/sessions").status_code == 200


def test_empty_token_means_unset(tmp_path):
    assert Settings(data_dir=tmp_path, api_token="  ").api_token is None
    with _client(tmp_path, api_token="") as c:
        assert c.get("/api/sessions").status_code == 200


def test_token_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("IRIS_API_TOKEN", TOKEN)
    monkeypatch.setenv("IRIS_PARENT_PID", "4242")
    s = Settings(data_dir=tmp_path)
    assert s.api_token is not None and s.api_token.get_secret_value() == TOKEN
    assert TOKEN not in repr(s)  # never printed with the settings
    assert s.parent_pid == 4242


@pytest.mark.parametrize("path", ["/api/health", "/api/sessions", "/openapi.json", "/nope"])
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Iris-Token": "wrong"},
        {"X-Iris-Token": ""},
        {"X-Iris-Token": TOKEN[:-1]},
        {"X-Iris-Token": TOKEN + "x"},
        {"Authorization": f"Bearer {TOKEN}"},
    ],
)
def test_missing_or_wrong_token_is_refused(tmp_path, path, headers):
    with _client(tmp_path, api_token=TOKEN) as c:
        res = c.get(path, headers=headers)
    assert res.status_code == 401
    assert res.json() == {"detail": "missing or wrong API token"}


def test_token_sent_twice_is_refused(tmp_path):
    with _client(tmp_path, api_token=TOKEN) as c:
        res = c.get("/api/health", headers=[("X-Iris-Token", TOKEN), ("X-Iris-Token", TOKEN)])
    assert res.status_code == 401


def test_right_token_is_accepted(tmp_path):
    with _client(tmp_path, api_token=TOKEN) as c:
        c.headers["X-Iris-Token"] = TOKEN
        assert c.get("/api/health").json()["ok"] is True
        assert c.get("/api/sessions").json() == []
        # A request with a body, and the header name in any case.
        live = c.post("/api/live", json={"title": "t"}, headers={"x-iris-token": TOKEN})
        assert live.status_code == 201
        sid = live.json()["id"]
        frame = c.post(
            f"/api/live/{sid}/frame", params={"t": 0}, content=b"\xff\xd8\xff" + b"0" * 64
        )
        assert frame.status_code == 204
        assert c.delete(f"/api/sessions/{sid}").status_code == 204


def test_refused_preflight_from_another_origin(tmp_path):
    """A web page on another origin can't get past the token, preflight or not."""
    with _client(tmp_path, api_token=TOKEN) as c:
        res = c.options(
            "/api/sessions",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
    assert res.status_code == 401


# --- parent watcher ---------------------------------------------------------------------------


def test_watch_parent_calls_on_gone_once_the_parent_is_gone():
    checks = iter([True, True, False])
    seen: list[int] = []
    gone: list[bool] = []

    def is_alive(pid: int) -> bool:
        seen.append(pid)
        return next(checks)

    asyncio.run(watch_parent(4242, 0.001, is_alive=is_alive, on_gone=lambda: gone.append(True)))
    assert seen == [4242, 4242, 4242]
    assert gone == [True]


def test_watch_parent_keeps_waiting_while_the_parent_lives():
    gone: list[bool] = []

    async def run() -> None:
        task = asyncio.create_task(
            watch_parent(1, 0.001, is_alive=lambda _: True, on_gone=lambda: gone.append(True))
        )
        await asyncio.sleep(0.05)
        assert not task.done()
        task.cancel()

    asyncio.run(run())
    assert gone == []


def test_pid_alive(monkeypatch):
    assert pid_alive(__import__("os").getpid())

    def kill(err):
        def _kill(pid, sig):
            raise err

        return _kill

    monkeypatch.setattr(iris.parent.os, "kill", kill(ProcessLookupError()))
    assert not pid_alive(4242)
    monkeypatch.setattr(iris.parent.os, "kill", kill(PermissionError()))
    assert pid_alive(4242)  # exists, but belongs to another user


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    until = time.monotonic() + timeout_s
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_server_shuts_down_when_the_parent_disappears(monkeypatch, tmp_path):
    alive = {"value": True}
    shutdowns: list[bool] = []
    monkeypatch.setattr(iris.parent, "pid_alive", lambda pid: pid == 4242 and alive["value"])
    monkeypatch.setattr(iris.parent, "request_shutdown", lambda: shutdowns.append(True))
    with _client(tmp_path, parent_pid=4242, parent_poll_s=0.01) as c:
        assert c.get("/api/health").status_code == 200
        time.sleep(0.05)
        assert shutdowns == []  # parent still there
        alive["value"] = False
        assert _wait_for(lambda: shutdowns == [True])
    assert shutdowns == [True]  # once, not on every poll


def test_no_parent_pid_means_no_watcher(monkeypatch, tmp_path):
    calls: list[int] = []
    monkeypatch.setattr(iris.parent, "pid_alive", lambda pid: calls.append(pid) or True)
    with _client(tmp_path, parent_poll_s=0.01) as c:
        c.get("/api/health")
        time.sleep(0.05)
    assert calls == []
