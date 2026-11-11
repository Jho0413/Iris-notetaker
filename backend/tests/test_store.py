"""SessionStore is shared by FastAPI's threadpool and the job runner, so it must be thread-safe."""

import sqlite3
import threading

import pytest

from iris.models import SessionStatus, SourceKind
from iris.store.db import SCHEMA, SessionStore


def test_concurrent_reads_and_writes_return_correct_rows(tmp_path):
    store = SessionStore(tmp_path / "iris.db")
    sessions = [store.create(f"session {i}", SourceKind.UPLOAD) for i in range(20)]
    ids = [s.id for s in sessions]
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def reader() -> None:
        try:
            start.wait()
            for _ in range(300):
                listed = store.list()
                assert set(ids) <= {s.id for s in listed}  # plus temporary sessions
                got = store.get(ids[0])
                assert got is not None and got.id == ids[0] and got.title == "session 0"
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    def writer() -> None:
        try:
            start.wait()
            for i in range(300):
                store.update(ids[i % len(ids)], status=SessionStatus.PROCESSING)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    def churner() -> None:
        try:
            start.wait()
            for i in range(100):
                temp = store.create(f"temp {i}", SourceKind.BROWSER_LIVE)
                assert temp is not None and temp.title == f"temp {i}"
                store.delete(temp.id)
        except BaseException as exc:  # noqa: BLE001 - collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(5)]
    threads += [threading.Thread(target=writer) for _ in range(2)]
    threads += [threading.Thread(target=churner)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    remaining = store.list()
    assert {s.id for s in remaining} == set(ids)  # every temporary session was deleted
    assert all(s.status == SessionStatus.PROCESSING for s in remaining)
    store.close()


# --- read-only (the MCP server) ---------------------------------------------------------------


def test_the_read_only_store_reads_but_never_writes(tmp_path):
    writer = SessionStore(tmp_path / "iris.db")
    sid = writer.create("Physics", SourceKind.UPLOAD).id
    reader = SessionStore(tmp_path / "iris.db", read_only=True)
    try:
        assert reader.get(sid).title == "Physics"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.update(sid, title="Changed")
    finally:
        reader.close()
        writer.close()


def test_the_read_only_store_never_creates_a_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        SessionStore(tmp_path / "missing" / "iris.db", read_only=True)
    assert not (tmp_path / "missing").exists()


def test_the_read_only_store_reads_a_database_from_before_newer_columns(tmp_path):
    """Opened read-only, an older database is never migrated: its sessions still read."""
    conn = sqlite3.connect(tmp_path / "iris.db")
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO sessions (id, title, source, status, created_at, updated_at)"
        " VALUES ('abc', 'Old', 'upload', 'done', '2026-10-01T00:00:00+00:00',"
        " '2026-10-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()
    reader = SessionStore(tmp_path / "iris.db", read_only=True)
    try:
        [session] = reader.list()
        assert (session.title, session.failure_message, session.failure_kind) == ("Old", None, None)
    finally:
        reader.close()
