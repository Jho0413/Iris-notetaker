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
