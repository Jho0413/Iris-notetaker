from pathlib import Path

import pytest

from iris.models import Notes, SessionType
from iris.store.layout import (
    SessionPaths,
    inside,
    read_model,
    read_optional,
    visual_path,
    visual_source_path,
    write_bytes,
    write_model,
)


def test_a_figure_has_its_source_crop_and_redraw_in_the_visuals_folder():
    assert visual_source_path("v000001") == "visuals/v000001.source.jpg"
    assert visual_path("v000001", "svg") == "visuals/v000001.svg"


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        ("visuals/v1.svg", "visuals/v1.svg"),
        ("frames/../visuals/v1.svg", "visuals/v1.svg"),
        ("../other/notes.json", None),
        ("/etc/passwd", None),
    ],
    ids=["inside", "inside-after-dots", "outside", "absolute"],
)
def test_a_path_must_stay_inside_the_session_folder(tmp_path, relative, expected):
    root = tmp_path / "s1"
    found = inside(root, relative)
    assert found == (None if expected is None else (root / expected).resolve())


def test_a_path_can_be_held_to_one_folder_of_the_session(tmp_path):
    root = tmp_path / "s1"
    assert inside(root, "frames/000001000.jpg", root / "frames") is not None
    assert inside(root, "visuals/v1.svg", root / "frames") is None


def test_a_link_out_of_the_session_folder_is_outside(tmp_path):
    root = tmp_path / "s1"
    (root / "visuals").mkdir(parents=True)
    (tmp_path / "secret.svg").write_text("<svg/>")
    (root / "visuals" / "v1.svg").symlink_to(tmp_path / "secret.svg")
    assert inside(root, "visuals/v1.svg") is None


def test_written_bytes_replace_the_file_whole_and_create_its_folder(tmp_path):
    path = tmp_path / "cache" / "k.png"
    write_bytes(path, b"first")
    write_bytes(path, b"second")
    assert path.read_bytes() == b"second"
    assert [p.name for p in path.parent.iterdir()] == ["k.png"]


def test_a_model_is_written_as_json_that_reads_back(tmp_path):
    notes = Notes(
        session_id="s1", session_type=SessionType.LECTURE, title="Café", markdown="x", visuals=[]
    )
    path = tmp_path / "new" / "notes.json"
    write_model(path, notes)
    assert read_model(path, Notes) == notes
    assert "Café" in path.read_text(encoding="utf-8")


def visuals(tmp_path: Path, *names: str) -> SessionPaths:
    paths = SessionPaths(tmp_path / "s1").ensure()
    for name in names:
        (paths.visuals_dir / name).write_bytes(b"x")
    return paths


def test_source_crops_not_in_the_selection_are_removed(tmp_path):
    paths = visuals(tmp_path, "v1.source.jpg", "v2.source.jpg", "v1.svg")
    paths.remove_source_crops_except({"visuals/v1.source.jpg"})
    assert sorted(p.name for p in paths.visuals_dir.iterdir()) == ["v1.source.jpg", "v1.svg"]


def test_redraws_not_rendered_are_removed_and_source_crops_are_kept(tmp_path):
    paths = visuals(
        tmp_path, "v1.svg", "v2.png", "v3.jpg", "v4.source.jpg", "v5.svg.tmp", "notes.txt"
    )
    paths.remove_redraws_except({"visuals/v1.svg"})
    assert sorted(p.name for p in paths.visuals_dir.iterdir()) == [
        "notes.txt",
        "v1.svg",
        "v4.source.jpg",
        "v5.svg.tmp",
    ]


def test_a_model_reads_back_whatever_the_locale_encoding(tmp_path, monkeypatch):
    path = tmp_path / "notes.json"
    notes = Notes(
        session_id="s1",
        session_type=SessionType.LECTURE,
        title="Café 会议",
        markdown="x",
        visuals=[],
    )
    write_model(path, notes)
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda self, encoding=None, errors=None: self.read_bytes().decode("latin-1"),
    )
    assert read_model(path, Notes).title == "Café 会议"


def test_a_failed_write_leaves_the_old_file_and_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "k.json"
    write_bytes(path, b"old")

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        write_bytes(path, b"new")
    assert path.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["k.json"]


def test_an_optional_file_that_is_missing_reads_as_none(tmp_path):
    assert read_optional(tmp_path / "absent.json", Notes) is None


def test_an_optional_file_that_is_damaged_reads_as_none_with_a_warning(tmp_path, caplog):
    path = tmp_path / "damaged.json"
    path.write_text("{not json")
    assert read_optional(path, Notes) is None
    assert f"ignoring unreadable {path}" in caplog.text


def test_an_optional_file_that_is_valid_reads_back(tmp_path):
    notes = Notes(
        session_id="s1", session_type=SessionType.LECTURE, title="t", markdown="m", visuals=[]
    )
    write_model(tmp_path / "notes.json", notes)
    assert read_optional(tmp_path / "notes.json", Notes) == notes


def test_an_optional_file_that_cannot_be_opened_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        read_optional(tmp_path, Notes)
