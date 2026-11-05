"""Backend support for the desktop app: ``python -m iris``, the per-launch API token, the secrets
passed on stdin (``iris serve --secrets-from-stdin``) and the parent watcher
(``IRIS_PARENT_PID``)."""

import asyncio
import io
import json
import os
import runpy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import iris.cli
import iris.parent
from iris.config import Settings
from iris.launch_secrets import MAX_LINE_BYTES, SecretsError, read_secrets, settings_with_secrets
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


# --- secrets on stdin -------------------------------------------------------------------------

KEY = "sk-from-the-app-0123456789"


def _line(**fields) -> bytes:
    return (json.dumps(fields) + "\n").encode()


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No key or token from the developer's environment or backend/.env: cwd is an empty folder."""
    for name in ("OPENAI_API_KEY", "IRIS_OPENAI_API_KEY", "IRIS_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.chdir(tmp_path)


def test_secrets_line_sets_key_and_token(clean_env):
    before = dict(os.environ)
    settings = settings_with_secrets(
        read_secrets(io.BytesIO(_line(openai_api_key=KEY, api_token=TOKEN)))
    )
    assert settings.openai_api_key == KEY
    assert settings.api_token is not None and settings.api_token.get_secret_value() == TOKEN
    assert dict(os.environ) == before  # never written into the environment


def test_secrets_line_with_no_key(clean_env):
    settings = settings_with_secrets(
        read_secrets(io.BytesIO(_line(openai_api_key=None, api_token=TOKEN)))
    )
    assert settings.openai_api_key is None
    assert settings.api_token is not None


def test_key_from_the_app_wins_over_env_and_dotenv(clean_env, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\nIRIS_API_TOKEN=dotenv-token\n")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    secrets = read_secrets(io.BytesIO(_line(openai_api_key=KEY, api_token=TOKEN)))
    settings = settings_with_secrets(secrets)
    assert settings.openai_api_key == KEY
    assert settings.api_token is not None and settings.api_token.get_secret_value() == TOKEN


def test_no_key_from_the_app_leaves_the_dotenv_key(clean_env, tmp_path):
    """As before: with no key stored in the app, a .env in the data folder may supply one."""
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-from-dotenv\n")
    secrets = read_secrets(io.BytesIO(_line(openai_api_key=None, api_token=TOKEN)))
    assert settings_with_secrets(secrets).openai_api_key == "sk-from-dotenv"


def test_reads_only_the_first_line():
    stream = io.BytesIO(_line(openai_api_key=KEY, api_token=TOKEN) + b"more input\n")
    assert read_secrets(stream).api_token.get_secret_value() == TOKEN
    assert stream.read() == b"more input\n"


def test_secrets_are_hidden_in_repr():
    secrets = read_secrets(io.BytesIO(_line(openai_api_key=KEY, api_token=TOKEN)))
    assert KEY not in repr(secrets) and TOKEN not in repr(secrets)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        (b"", "no secrets line"),
        (b"\n", "no secrets line"),
        (b"   \n", "no secrets line"),
        (f'{{"openai_api_key": "{KEY}", "api_token": '.encode(), "not valid"),  # cut short
        (f"{KEY} {TOKEN}\n".encode(), "not valid"),
        (_line(openai_api_key=KEY), "api_token"),  # token missing
        (_line(openai_api_key=KEY, api_token=""), "api_token"),
        (_line(openai_api_key=KEY, api_token="   "), "api_token"),
        (_line(openai_api_key="", api_token=TOKEN), "openai_api_key"),
        (_line(openai_api_key=42, api_token=TOKEN), "openai_api_key"),
        (_line(api_token=TOKEN), "openai_api_key"),  # the key is always sent, null if none
        (_line(openai_api_key=KEY, api_token=TOKEN, **{KEY + "x": 1}), "unknown field"),
        (json.dumps([KEY, TOKEN]).encode() + b"\n", "not valid"),
        (_line(openai_api_key=KEY, api_token=TOKEN + "x" * MAX_LINE_BYTES), "longer than"),
    ],
)
def test_bad_secrets_line_fails_clearly_without_the_secrets(line, problem):
    with pytest.raises(SecretsError) as exc:
        read_secrets(io.BytesIO(line))
    message = str(exc.value)
    assert problem in message
    assert KEY not in message and TOKEN not in message
    # No chained pydantic error, whose text would hold the input.
    assert exc.value.__cause__ is None
    assert exc.value.__context__ is None or exc.value.__suppress_context__


def _serve(monkeypatch, stdin: bytes, *flags: str) -> SimpleNamespace:
    """Run ``iris serve --secrets-from-stdin`` with uvicorn replaced: what it would have served."""
    served = SimpleNamespace(app=None, code=None)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(stdin)))
    monkeypatch.setattr(sys, "argv", ["iris", "serve", "--secrets-from-stdin", *flags])
    monkeypatch.setattr("uvicorn.run", lambda app, **_: setattr(served, "app", app))
    with pytest.raises(SystemExit) as exit_:
        iris.cli.main()
    served.code = exit_.value.code
    return served


def test_serve_with_secrets_from_stdin(clean_env, monkeypatch, tmp_path):
    before = dict(os.environ)
    served = _serve(monkeypatch, _line(openai_api_key=KEY, api_token=TOKEN))
    assert served.code == 0
    assert dict(os.environ) == before
    with TestClient(served.app) as c:
        assert c.get("/api/settings").status_code == 401  # the token from stdin guards the API
        c.headers["X-Iris-Token"] = TOKEN
        assert c.get("/api/settings").json()["api_key_set"] is True
        # A save on the Settings page rebuilds the settings from the loaded ones: the key stays.
        saved = c.put("/api/settings", json={"transcribe_language": "fr"})
        assert saved.status_code == 200
        assert saved.json()["api_key_set"] is True
        assert served.app.state.runner.settings.openai_api_key == KEY
        assert served.app.state.runner.settings.transcribe_language == "fr"


def test_serve_with_no_key_from_stdin(clean_env, monkeypatch):
    served = _serve(monkeypatch, _line(openai_api_key=None, api_token=TOKEN))
    with TestClient(served.app, headers={"X-Iris-Token": TOKEN}) as c:
        assert c.get("/api/settings").json()["api_key_set"] is False


@pytest.mark.parametrize(
    "stdin", [b"", b"not json\n", _line(openai_api_key=KEY), _line(openai_api_key=KEY, x=TOKEN)]
)
def test_serve_refuses_a_bad_secrets_line(clean_env, monkeypatch, capsys, stdin):
    served = _serve(monkeypatch, stdin)
    assert served.code == 2
    assert served.app is None  # never started
    err = capsys.readouterr().err
    assert err.startswith("iris serve: ")
    assert KEY not in err and TOKEN not in err


def test_serve_refuses_reload_with_secrets_from_stdin(clean_env, monkeypatch, capsys):
    served = _serve(monkeypatch, _line(openai_api_key=KEY, api_token=TOKEN), "--reload")
    assert served.code == 2
    assert served.app is None
    assert "--reload" in capsys.readouterr().err


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
