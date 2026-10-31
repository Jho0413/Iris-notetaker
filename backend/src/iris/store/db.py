"""SQLite index of sessions. The files under sessions/<id>/ are the source of truth for content;
this table only tracks what sessions exist and their status."""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from iris.models import Session, SessionStatus, SourceKind, Stage

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    source        TEXT NOT NULL,
    status        TEXT NOT NULL,
    current_stage TEXT,
    error         TEXT,
    duration_s    REAL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_created_at ON sessions (created_at DESC);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """Small synchronous wrapper. Single user and tiny queries, so no async driver needed."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def create(self, title: str, source: SourceKind) -> Session:
        now = _now()
        sid = uuid.uuid4().hex[:12]
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, title, source, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, title, source.value, SessionStatus.CREATED.value, now, now),
            )
        return self.get(sid)  # type: ignore[return-value]

    def get(self, session_id: str) -> Session | None:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return Session(**dict(row)) if row else None

    def list(self) -> list[Session]:
        rows = self._conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [Session(**dict(r)) for r in rows]

    def update(
        self,
        session_id: str,
        *,
        status: SessionStatus | None = None,
        current_stage: Stage | None = None,
        error: str | None = None,
        duration_s: float | None = None,
        title: str | None = None,
    ) -> None:
        fields: dict[str, object] = {"updated_at": _now()}
        if status is not None:
            fields["status"] = status.value
        if current_stage is not None:
            fields["current_stage"] = current_stage.value
        if error is not None:
            fields["error"] = error
        if duration_s is not None:
            fields["duration_s"] = duration_s
        if title is not None:
            fields["title"] = title
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._conn:
            self._conn.execute(
                f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id)
            )

    def clear_error(self, session_id: str) -> None:
        with self._conn:
            self._conn.execute("UPDATE sessions SET error = NULL WHERE id = ?", (session_id,))

    def delete(self, session_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
