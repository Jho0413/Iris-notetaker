import asyncio
import threading
import time
from collections.abc import Callable

from fastapi.testclient import TestClient

from iris.live.audio import LiveDecoder


def wait_until[T](predicate: Callable[[], T], timeout: float, what: str) -> T:
    deadline = time.monotonic() + timeout
    while not (value := predicate()):
        assert time.monotonic() < deadline, what
        time.sleep(0.05)
    return value


def wait_for_status(client: TestClient, sid: str, statuses: set[str], timeout: float = 5.0) -> dict:
    def reached() -> dict | None:
        session = client.get(f"/api/sessions/{sid}").json()
        return session if session["status"] in statuses else None

    return wait_until(reached, timeout, f"session {sid} never reached {statuses}")


def wait_for_stage(client: TestClient, sid: str, stage: str, timeout: float = 5.0) -> None:
    def reached() -> bool:
        return client.get(f"/api/sessions/{sid}").json()["current_stage"] == stage

    wait_until(reached, timeout, f"session {sid} never reached {stage}")


def wait_until_idle(client: TestClient, sid: str, timeout: float = 10.0) -> dict:
    runner = client.app.state.runner  # type: ignore[attr-defined]
    wait_until(lambda: not runner.is_running(sid), timeout, f"session {sid} never finished")
    return client.get(f"/api/sessions/{sid}").json()


def wait_for_done(client: TestClient, sid: str, timeout: float = 30.0) -> dict:
    runner = client.app.state.runner  # type: ignore[attr-defined]

    def finished() -> dict | None:
        session = client.get(f"/api/sessions/{sid}").json()
        done = session["status"] in {"done", "failed"} and not runner.is_running(sid)
        return session if done else None

    return wait_until(finished, timeout, f"session {sid} did not finish")


async def eventually(predicate: Callable[[], object], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


async def settle(turns: int) -> None:
    for _ in range(turns):
        await asyncio.sleep(0)


def decoder_waiting_for(monkeypatch, seq: int) -> threading.Event:
    waiting = threading.Event()
    next_chunk = LiveDecoder.next_chunk

    def watched(decoder, n):
        if n == seq:
            decoder._report()
            waiting.set()
        return next_chunk(decoder, n)

    monkeypatch.setattr(LiveDecoder, "next_chunk", watched)
    return waiting
