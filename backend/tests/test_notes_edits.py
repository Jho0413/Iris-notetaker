from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from iris.main import create_app
from iris.models import FrameRef, Notes, NotesEdits, NotesVisual, SessionType, SourceKind
from iris.notes_edits import MAX_EDITED_CHARS, generated_hash, newest
from iris.store.layout import read_model, session_paths, write_model
from tests.test_export import images

GENERATED = ":::box[Problem]{color=indigo}\nFind $x$.\n:::\n\n::figure{#v1}"
EDITED = ":::box[My problem]{color=teal}\nFind $x$, then $y$.\n:::\n\n::figure{#v1}"


def make_notes(client, settings, markdown=GENERATED, format_version=2) -> tuple[str, Notes]:
    session = client.app.state.store.create("Loops", SourceKind.UPLOAD)
    paths = session_paths(settings.sessions_dir, session.id).ensure()
    (paths.visuals_dir / "v1.jpg").write_bytes(b"image")
    notes = Notes(
        session_id=session.id,
        session_type=SessionType.LECTURE,
        title="Loops",
        markdown=markdown,
        visuals=[
            NotesVisual(
                id="v1",
                moment_id="m1",
                frame=FrameRef(t=0, path="frames/0.jpg"),
                caption="Forces",
                path="visuals/v1.jpg",
            )
        ],
        format_version=format_version,
    )
    write_model(paths.notes, notes)
    return session.id, notes


def save(client, sid, markdown, based_on):
    return client.put(
        f"/api/sessions/{sid}/notes/edits", json={"markdown": markdown, "based_on": based_on}
    )


def test_generated_notes_say_which_version_they_are_and_are_not_edited(client, settings):
    sid, notes = make_notes(client, settings)
    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["markdown"] == GENERATED
    assert got["generated_hash"] == generated_hash(notes)
    assert got["edited"] is None


def test_save_then_read_restore_and_the_generated_notes_are_kept(client, settings):
    sid, notes = make_notes(client, settings)
    paths = session_paths(settings.sessions_dir, sid)
    on_disk = paths.notes.read_text()
    stamp = generated_hash(notes)

    saved = save(client, sid, EDITED, stamp)
    assert saved.status_code == 200
    assert saved.json()["markdown"] == EDITED
    assert saved.json()["edited"]["stale"] is False

    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["markdown"] == EDITED and got["edited"]["saved_at"]
    assert paths.notes.read_text() == on_disk

    assert save(client, sid, EDITED + "\n\nMore.", stamp).status_code == 200
    assert read_model(paths.notes_edits, NotesEdits).markdown.endswith("More.")
    assert client.get(f"/api/sessions/{sid}/notes").json()["markdown"].endswith("More.")

    assert client.delete(f"/api/sessions/{sid}/notes/edits").status_code == 204
    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["markdown"] == GENERATED and got["edited"] is None
    assert not paths.notes_edits.exists()
    assert client.delete(f"/api/sessions/{sid}/notes/edits").status_code == 204


@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        (":::box[Open]{color=teal}\nNo end.", "Line 1: This block isn't closed."),
        ("::figure{#v9}", 'Line 1: There\'s no figure "v9" in this session.'),
        ("Text.\n\n<script>alert(1)</script>", "Line 3: HTML isn't supported in notes."),
        ("![x](https://example.com/x.png)", "Line 1: Images from outside the session"),
        (":::box[A]{color=purple}\nx\n:::", "Line 1: \"purple\" isn't one of Iris's colours."),
    ],
)
def test_invalid_notes_are_refused_in_plain_words(client, settings, markdown, message):
    sid, notes = make_notes(client, settings)
    response = save(client, sid, markdown, generated_hash(notes))
    assert response.status_code == 422
    assert response.json()["detail"].startswith(message)
    assert not session_paths(settings.sessions_dir, sid).notes_edits.exists()


def test_bad_requests(client, settings):
    sid, notes = make_notes(client, settings)
    stamp = generated_hash(notes)
    url = f"/api/sessions/{sid}/notes/edits"
    assert save(client, sid, "x" * (MAX_EDITED_CHARS + 1), stamp).status_code == 422
    assert client.put(url, json={"markdown": "x", "based_on": ""}).status_code == 422
    assert client.put(url, json={"markdown": "x"}).status_code == 422
    extra = {"markdown": "x", "based_on": stamp, "visuals": []}
    assert client.put(url, json=extra).status_code == 422
    assert save(client, "nope", "x", stamp).status_code == 404
    assert client.delete("/api/sessions/nope/notes/edits").status_code == 404
    other = client.app.state.store.create("Empty", SourceKind.UPLOAD)
    session_paths(settings.sessions_dir, other.id).ensure()
    assert save(client, other.id, "x", stamp).status_code == 404


def test_notes_not_made_yet_are_not_ready_to_read_or_save_even_while_processing(
    client, settings, monkeypatch
):
    session = client.app.state.store.create("Empty", SourceKind.UPLOAD)
    session_paths(settings.sessions_dir, session.id).ensure()
    monkeypatch.setattr(client.app.state.runner, "is_running", lambda _: True)
    for r in (client.get(f"/api/sessions/{session.id}/notes"), save(client, session.id, "x", "s")):
        assert r.status_code == 404
        assert r.json()["detail"] == "notes not ready"


def test_no_saving_or_restoring_while_the_session_is_processing(client, settings, monkeypatch):
    sid, notes = make_notes(client, settings)
    monkeypatch.setattr(client.app.state.runner, "is_running", lambda _: True)
    assert save(client, sid, EDITED, generated_hash(notes)).status_code == 409
    assert client.delete(f"/api/sessions/{sid}/notes/edits").status_code == 409


def test_export_uses_the_edits(client, settings):
    sid, notes = make_notes(client, settings)
    save(client, sid, EDITED, generated_hash(notes))
    exported = client.get(f"/api/sessions/{sid}/export").text
    assert ":::box[My problem]{color=teal}" in exported
    assert [i.attrGet("src") for i in images(exported)] == ["data:image/jpeg;base64,aW1hZ2U="]


def test_a_rerun_keeps_the_edits_and_reports_them_stale_until_the_user_chooses(client, settings):
    sid, notes = make_notes(client, settings)
    paths = session_paths(settings.sessions_dir, sid)
    save(client, sid, EDITED, generated_hash(notes))

    rerun = notes.model_copy(update={"markdown": GENERATED + "\n\nNew paragraph."})
    write_model(paths.notes, rerun)
    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["markdown"] == EDITED
    assert got["edited"]["stale"] is True
    assert got["generated_hash"] == generated_hash(rerun)

    kept = save(client, sid, got["markdown"], got["generated_hash"]).json()
    assert kept["markdown"] == EDITED and kept["edited"]["stale"] is False

    write_model(paths.notes, notes)
    assert client.get(f"/api/sessions/{sid}/notes").json()["edited"]["stale"] is True
    client.delete(f"/api/sessions/{sid}/notes/edits")
    assert client.get(f"/api/sessions/{sid}/notes").json()["markdown"] == GENERATED


def test_edits_of_old_notes_are_stale_only_when_the_notes_change(client, settings):
    sid, notes = make_notes(client, settings, markdown="**Answer:** 42.", format_version=1)
    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["markdown"] == ":::box[Answer]{color=green}\n42.\n:::"
    saved = save(client, sid, got["markdown"] + "\n\nMine.", got["generated_hash"]).json()
    assert saved["edited"]["stale"] is False


def test_edits_survive_an_engine_restart(settings):
    with TestClient(create_app(settings)) as first:
        sid, notes = make_notes(first, settings)
        assert save(first, sid, EDITED, generated_hash(notes)).status_code == 200
    with TestClient(create_app(settings)) as second:
        got = second.get(f"/api/sessions/{sid}/notes").json()
        assert got["markdown"] == EDITED and got["edited"]["stale"] is False


def test_damaged_edits_leave_the_generated_notes_readable(client, settings, caplog):
    sid, _ = make_notes(client, settings)
    session_paths(settings.sessions_dir, sid).notes_edits.write_text("{not json")
    got = client.get(f"/api/sessions/{sid}/notes")
    assert got.status_code == 200 and got.json()["markdown"] == GENERATED
    assert "unreadable" in caplog.text


def test_after_a_rerun_renumbers_a_figure_keep_is_refused_until_the_edits_are_fixed(
    client, settings
):
    sid, notes = make_notes(client, settings)
    paths = session_paths(settings.sessions_dir, sid)
    save(client, sid, EDITED, generated_hash(notes))
    renumbered = notes.model_copy(
        update={
            "markdown": GENERATED.replace("#v1", "#v2"),
            "visuals": [notes.visuals[0].model_copy(update={"id": "v2"})],
        }
    )
    write_model(paths.notes, renumbered)
    got = client.get(f"/api/sessions/{sid}/notes").json()
    assert got["edited"]["stale"] is True

    kept = save(client, sid, got["markdown"], got["generated_hash"])
    assert kept.status_code == 422
    assert "no figure" in kept.json()["detail"]
    assert client.get(f"/api/sessions/{sid}/notes").json()["edited"]["stale"] is True

    fixed = save(client, sid, EDITED.replace("#v1", "#v2"), got["generated_hash"]).json()
    assert fixed["edited"]["stale"] is False


def test_edits_that_cant_be_opened_are_never_passed_over(settings):
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        sid, notes = make_notes(client, settings)
        assert save(client, sid, EDITED, generated_hash(notes)).status_code == 200
        path = session_paths(settings.sessions_dir, sid).notes_edits
        path.unlink()
        path.mkdir()
        assert client.get(f"/api/sessions/{sid}/notes").status_code == 500
        assert client.get(f"/api/sessions/{sid}/export").status_code == 500


def damage(path):
    path.write_text("{not json")


def make_unopenable(path):
    path.unlink()
    path.mkdir()


@pytest.mark.parametrize("spoil", [damage, make_unopenable], ids=["damaged", "unopenable"])
@pytest.mark.parametrize(
    "ask",
    [
        lambda client, sid, stamp: client.get(f"/api/sessions/{sid}/notes"),
        lambda client, sid, stamp: client.get(f"/api/sessions/{sid}/export"),
        lambda client, sid, stamp: save(client, sid, EDITED, stamp),
    ],
    ids=["read", "export", "save"],
)
def test_notes_that_cant_be_read_are_refused_in_plain_words_and_logged(
    client, settings, caplog, spoil, ask
):
    sid, notes = make_notes(client, settings)
    spoil(session_paths(settings.sessions_dir, sid).notes)
    r = ask(client, sid, generated_hash(notes))
    assert r.status_code == 500
    assert r.json()["detail"] == "the notes couldn't be read"
    assert f"can't read the notes of session {sid}" in caplog.text


def test_the_newest_version_is_the_edits_until_the_notes_are_made_again():
    generated = Notes(
        session_id="s1",
        session_type=SessionType.LECTURE,
        title="Loops",
        markdown=GENERATED,
        visuals=[],
        format_version=2,
    )
    saved = datetime.now(UTC)
    current = NotesEdits(markdown=EDITED, based_on=generated_hash(generated), saved_at=saved)
    assert newest(generated, current).markdown == EDITED
    stale = NotesEdits(markdown=EDITED, based_on="older notes", saved_at=saved)
    assert newest(generated, stale).markdown == GENERATED
    assert newest(generated, stale).edited is None
    assert newest(generated, None).markdown == GENERATED
