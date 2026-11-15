import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

from iris.models import (
    GENERIC_FAILURE,
    FailureKind,
    FailureMessage,
    Session,
    SessionStatus,
    SourceKind,
    Stage,
)

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

COLUMNS_ADDED_SINCE_FIRST_SCHEMA = {
    "failure_message": "TEXT",
    "failure_kind": "TEXT",
    "error_detail": "TEXT",
    "failed_at": "TEXT",
}
SESSION_FIELDS = tuple(Session.model_fields)
FAILURE_MESSAGES = frozenset(get_args(FailureMessage))
FAILURE_KINDS = frozenset(FailureKind)
FAILURE_COLUMNS = ("failure_message", "failure_kind", "error_detail", "failed_at", "error")


@dataclass(frozen=True)
class FailureDetail:
    error: str | None
    at: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    def __init__(self, db_path: Path, *, read_only: bool = False):
        self._one_query_at_a_time = threading.RLock()
        if read_only:
            target = f"{db_path.resolve().as_uri()}?mode=ro"
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            target = str(db_path)
        self._conn = sqlite3.connect(target, uri=read_only, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if read_only:
            return
        with self._one_query_at_a_time:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        with self._conn:
            for column, kind in COLUMNS_ADDED_SINCE_FIRST_SCHEMA.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {kind}")
            self._conn.execute(
                "UPDATE sessions SET error_detail = COALESCE(error_detail, error),"
                " failure_message = COALESCE(failure_message, ?),"
                " failure_kind = COALESCE(failure_kind, ?), error = NULL"
                " WHERE error IS NOT NULL",
                (GENERIC_FAILURE, FailureKind.GENERAL.value),
            )

    @staticmethod
    def _session(row: sqlite3.Row) -> Session:
        column_names = set(row.keys())
        stored_fields = {k: row[k] for k in SESSION_FIELDS if k in column_names}
        if stored_fields["failure_message"] is not None:
            if stored_fields["failure_message"] not in FAILURE_MESSAGES:
                stored_fields["failure_message"] = GENERIC_FAILURE
                stored_fields["failure_kind"] = FailureKind.GENERAL
            elif stored_fields["failure_kind"] not in FAILURE_KINDS:
                stored_fields["failure_kind"] = FailureKind.GENERAL
        elif stored_fields["failure_kind"] is not None:
            stored_fields["failure_kind"] = None
        return Session(**stored_fields)

    def close(self) -> None:
        with self._one_query_at_a_time:
            self._conn.close()

    def create(self, title: str, source: SourceKind) -> Session:
        now = _now()
        sid = uuid.uuid4().hex[:12]
        with self._one_query_at_a_time, self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, title, source, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, title, source.value, SessionStatus.CREATED.value, now, now),
            )
        return self.get(sid)  # type: ignore[return-value]

    def get(self, session_id: str) -> Session | None:
        with self._one_query_at_a_time:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._session(row) if row else None

    def list(self) -> list[Session]:
        with self._one_query_at_a_time:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [self._session(r) for r in rows]

    def _set(self, session_id: str, fields: dict[str, object]) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._one_query_at_a_time, self._conn:
            self._conn.execute(
                f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id)
            )

    def update(
        self,
        session_id: str,
        *,
        status: SessionStatus | None = None,
        current_stage: Stage | None = None,
        duration_s: float | None = None,
        title: str | None = None,
    ) -> None:
        fields: dict[str, object] = {"updated_at": _now()}
        if status is not None:
            fields["status"] = status.value
        if current_stage is not None:
            fields["current_stage"] = current_stage.value
        if duration_s is not None:
            fields["duration_s"] = duration_s
        if title is not None:
            fields["title"] = title
        self._set(session_id, fields)

    def fail(
        self,
        session_id: str,
        *,
        message: FailureMessage,
        kind: FailureKind,
        detail: str,
        stage: Stage | None = None,
    ) -> None:
        now = _now()
        fields: dict[str, object] = {
            "status": SessionStatus.FAILED.value,
            "failure_message": message,
            "failure_kind": kind.value,
            "error_detail": detail,
            "failed_at": now,
            "updated_at": now,
        }
        if stage is not None:
            fields["current_stage"] = stage.value
        self._set(session_id, fields)

    def clear_error(self, session_id: str) -> None:
        self._set(session_id, dict.fromkeys(FAILURE_COLUMNS))

    def failure_detail(self, session_id: str) -> FailureDetail | None:
        with self._one_query_at_a_time:
            row = self._conn.execute(
                "SELECT error_detail, failed_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None or row["error_detail"] is None:
            return None
        return FailureDetail(error=row["error_detail"], at=row["failed_at"])

    def delete(self, session_id: str) -> None:
        with self._one_query_at_a_time, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
