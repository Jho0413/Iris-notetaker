import sys

import pytest

from iris import cli
from iris.config import get_settings
from iris.models import Moments, SourceKind, Transcript
from iris.store.db import SessionStore
from iris.store.layout import session_paths, write_model


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIS_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def iris(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["iris", *args])
    with pytest.raises(SystemExit) as exit_:
        cli.main()
    return exit_.value.code


@pytest.mark.parametrize(
    "args",
    [("run", "nope"), ("run", "nope", "--from", "select"), ("stage", "nope", "align")],
    ids=["run", "run-from", "stage"],
)
def test_an_unknown_session_is_named_and_exits_1(data_dir, monkeypatch, capsys, args):
    assert iris(monkeypatch, *args) == 1
    assert capsys.readouterr().err == "no session nope\n"


def test_a_stage_of_a_known_session_runs(data_dir, monkeypatch, capsys):
    settings = get_settings()
    settings.sessions_dir.mkdir(parents=True)
    store = SessionStore(settings.db_path)
    session = store.create("Loops", SourceKind.UPLOAD)
    store.close()
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    write_model(paths.moments, Moments(moments=[]))
    write_model(paths.transcript, Transcript(segments=[]))

    assert iris(monkeypatch, "stage", session.id, "align") == 0
    assert capsys.readouterr().out == "align done\n"
    assert paths.alignment.exists()
