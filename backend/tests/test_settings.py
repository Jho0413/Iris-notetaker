"""Settings page API and the saved settings file (iris/user_settings.py)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iris.config import Settings
from iris.main import create_app
from iris.user_settings import (
    UserSettings,
    apply_user_settings,
    load_user_settings,
    save_user_settings,
)


@pytest.fixture(autouse=True)
def no_language_env(monkeypatch):
    """Each test decides itself whether the language is pinned by the environment."""
    monkeypatch.delenv("IRIS_TRANSCRIBE_LANGUAGE", raising=False)


def make_settings(tmp_path: Path, **kwargs) -> Settings:
    return Settings(data_dir=tmp_path / "data", _env_file=None, **kwargs)


def app_client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def language(client: TestClient) -> dict:
    r = client.get("/api/settings")
    assert r.status_code == 200
    return r.json()["transcribe_language"]


# --- The file ---------------------------------------------------------------------------------


def test_missing_file_means_nothing_saved(tmp_path: Path):
    assert load_user_settings(tmp_path / "settings.json") == UserSettings()


def test_save_then_load_round_trips(tmp_path: Path):
    path = tmp_path / "new" / "settings.json"  # the data folder may not exist yet
    save_user_settings(path, UserSettings(transcribe_language="fr"))
    assert load_user_settings(path).transcribe_language == "fr"
    assert not path.with_suffix(".json.tmp").exists()


@pytest.mark.parametrize(
    "content",
    [
        b"{not json",
        b"",
        b'["fr"]',
        b'"fr"',
        b"\xff\xfe\x00garbage",
    ],
)
def test_damaged_file_is_ignored(tmp_path: Path, content: bytes, caplog):
    path = tmp_path / "settings.json"
    path.write_bytes(content)
    assert load_user_settings(path) == UserSettings()
    assert "ignoring" in caplog.text


def test_invalid_value_and_unknown_keys_are_ignored(tmp_path: Path, caplog):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"transcribe_language": "jp", "from_a_newer_iris": 1}))
    assert load_user_settings(path) == UserSettings()
    assert "transcribe_language" in caplog.text


@pytest.mark.parametrize(("saved", "applied"), [("fr", "fr"), (" DE ", "de"), ("auto", None)])
def test_saved_language_applies(tmp_path: Path, saved: str, applied: str | None):
    user = UserSettings(transcribe_language=saved)
    assert apply_user_settings(make_settings(tmp_path), user).transcribe_language == applied


def test_environment_wins_over_saved(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IRIS_TRANSCRIBE_LANGUAGE", "ja")
    user = UserSettings(transcribe_language="fr")
    assert apply_user_settings(make_settings(tmp_path), user).transcribe_language == "ja"


def test_dotenv_wins_over_saved(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("IRIS_TRANSCRIBE_LANGUAGE=es\n")
    settings = Settings(data_dir=tmp_path / "data", _env_file=env_file)
    user = UserSettings(transcribe_language="fr")
    assert apply_user_settings(settings, user).transcribe_language == "es"


# --- The API ----------------------------------------------------------------------------------


def test_defaults(tmp_path: Path):
    settings = make_settings(tmp_path)
    with app_client(settings) as client:
        body = client.get("/api/settings").json()
    assert body["transcribe_language"] == {
        "value": "en",
        "default": "en",
        "source": "default",
        "env_var": "IRIS_TRANSCRIBE_LANGUAGE",
    }
    assert "fr" in body["languages"] and "auto" not in body["languages"]
    assert body["languages"] == sorted(body["languages"])
    assert body["data_dir"] == str(settings.data_dir.resolve())
    assert body["version"]


def test_save_applies_to_the_next_session_and_survives_a_restart(tmp_path: Path):
    settings = make_settings(tmp_path)
    with app_client(settings) as client:
        r = client.put("/api/settings", json={"transcribe_language": "fr"})
        assert r.status_code == 200
        assert r.json()["transcribe_language"]["value"] == "fr"
        assert r.json()["transcribe_language"]["source"] == "saved"
        # No restart: the next session's stages see the new language.
        sid = client.post("/api/live", json={"title": "cours"}).json()["id"]
        ctx = client.app.state.runner.context(sid)
        assert ctx.settings.transcribe_language == "fr"
    saved = json.loads(settings.user_settings_path.read_text())
    assert saved["transcribe_language"] == "fr"
    with app_client(settings) as client:  # an engine restart
        assert language(client)["value"] == "fr"
        assert client.app.state.runner.settings.transcribe_language == "fr"


def test_auto_detect_can_be_saved(tmp_path: Path):
    with app_client(make_settings(tmp_path)) as client:
        r = client.put("/api/settings", json={"transcribe_language": "AUTO"})
        assert r.json()["transcribe_language"]["value"] == "auto"
        assert client.app.state.runner.settings.transcribe_language is None


@pytest.mark.parametrize("value", ["jp", "english", "en-GB", "", "  ", "zz"])
def test_unknown_language_is_rejected_with_a_clear_message(tmp_path: Path, value: str):
    settings = make_settings(tmp_path)
    with app_client(settings) as client:
        r = client.put("/api/settings", json={"transcribe_language": value})
        assert language(client)["value"] == "en"
    assert r.status_code == 422
    assert "not a language Whisper knows" in r.text or "is empty" in r.text
    assert not settings.user_settings_path.exists()


def test_unknown_fields_are_rejected(tmp_path: Path):
    with app_client(make_settings(tmp_path)) as client:
        r = client.put("/api/settings", json={"notes_model": "gpt-4o"})
    assert r.status_code == 422


def test_empty_update_changes_nothing(tmp_path: Path):
    settings = make_settings(tmp_path)
    with app_client(settings) as client:
        r = client.put("/api/settings", json={})
    assert r.status_code == 200
    assert not settings.user_settings_path.exists()


def test_pinned_language_is_shown_and_cant_be_changed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IRIS_TRANSCRIBE_LANGUAGE", "ja")
    settings = make_settings(tmp_path)
    save_user_settings(settings.user_settings_path, UserSettings(transcribe_language="fr"))
    with app_client(settings) as client:
        assert language(client) == {
            "value": "ja",
            "default": "en",
            "source": "pinned",
            "env_var": "IRIS_TRANSCRIBE_LANGUAGE",
        }
        r = client.put("/api/settings", json={"transcribe_language": "de"})
        assert r.status_code == 409
        assert client.app.state.runner.settings.transcribe_language == "ja"
    assert load_user_settings(settings.user_settings_path).transcribe_language == "fr"


def test_damaged_file_still_starts_and_the_next_save_repairs_it(tmp_path: Path):
    settings = make_settings(tmp_path)
    settings.user_settings_path.parent.mkdir(parents=True)
    settings.user_settings_path.write_text("{oops")
    with app_client(settings) as client:
        assert language(client)["source"] == "default"
        assert client.put("/api/settings", json={"transcribe_language": "it"}).status_code == 200
    assert load_user_settings(settings.user_settings_path).transcribe_language == "it"


def test_failed_write_keeps_the_old_settings(tmp_path: Path, monkeypatch):
    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr("iris.api.routes.settings.save_user_settings", fail)
    with app_client(make_settings(tmp_path)) as client:
        r = client.put("/api/settings", json={"transcribe_language": "fr"})
        assert r.status_code == 500
        assert "disk full" not in r.text
        assert language(client)["value"] == "en"
        assert client.app.state.runner.settings.transcribe_language == "en"


# --- The API key (only whether one is set) ----------------------------------------------------


@pytest.mark.parametrize(
    ("key", "expected"), [(None, False), ("", False), ("  ", False), ("sk-x", True)]
)
def test_reports_whether_a_key_is_set(tmp_path: Path, key, expected: bool):
    with app_client(make_settings(tmp_path, openai_api_key=key)) as client:
        body = client.get("/api/settings").json()
    assert body["api_key_set"] is expected


def test_the_key_itself_never_reaches_the_api(tmp_path: Path):
    secret = "sk-test-do-not-leak-1234567890"
    with app_client(make_settings(tmp_path, openai_api_key=secret)) as client:
        for r in (
            client.get("/api/settings"),
            client.put("/api/settings", json={"transcribe_language": "fr"}),
        ):
            assert r.status_code == 200
            assert secret not in r.text
