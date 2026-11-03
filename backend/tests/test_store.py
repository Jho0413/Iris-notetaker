"""SessionStore is shared by FastAPI's threadpool and the job runner, so it must be thread-safe."""

import threading

from iris.models import SessionStatus, SourceKind
from iris.store.db import SessionStore


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
                assert {s.id for s in listed} == set(ids)
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

    threads = [threading.Thread(target=reader) for _ in range(6)]
    threads += [threading.Thread(target=writer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert all(s.status == SessionStatus.PROCESSING for s in store.list())
    store.close()
