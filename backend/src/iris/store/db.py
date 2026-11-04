"""SQLite index of sessions. The files under sessions/<id>/ are the source of truth for content;
this table only tracks what sessions exist and their status.

A failed run stores what the user is told (``failure_message``, ``failure_kind``) apart from what
it failed on (``error_detail``, ``failed_at``), which is for developers only: it is never part of
the API's ``Session`` and is read with ``failure_detail`` (``iris show``).
"""

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
    error         TEXT,  -- older versions' raw error; migrated to error_detail on open
    duration_s    REAL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_created_at ON sessions (created_at DESC);
"""

# Columns added since the first schema, created on open if missing.
ADDED_COLUMNS = {
    "failure_message": "TEXT",
    "failure_kind": "TEXT",
    "error_detail": "TEXT",
    "failed_at": "TEXT",
}
SESSION_FIELDS = tuple(Session.model_fields)
FAILURE_MESSAGES = frozenset(get_args(FailureMessage))
FAILURE_KINDS = frozenset(FailureKind)


@dataclass(frozen=True)
class FailureDetail:
    """What a failed run failed on, for developers (never served by the API)."""

    error: str | None
    at: str | None  # ISO time of the failure; None for sessions failed before it was recorded


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionStore:
    """Small synchronous wrapper. Single user and tiny queries, so no async driver needed.

    One connection is shared by FastAPI's threadpool (sync routes) and the event loop (the job
    runner). A sqlite3 connection must not be used by two threads at once: concurrent queries
    interleave their cursors and return mixed-up rows or raise ``InterfaceError``. Every use of
    the connection therefore holds ``_lock``. Queries are tiny, so contention is negligible.
    """

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()  # re-entrant, so a locked method may call another
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Add new columns, and move older versions' raw ``error`` text out of what is served:
        it becomes the developer-only ``error_detail``, and the user sees the generic message."""
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(sessions)")}
        with self._conn:
            for column, kind in ADDED_COLUMNS.items():
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
        fields = {k: row[k] for k in SESSION_FIELDS}
        # A stored message no longer on the list (reworded since, or written by mistake) is served
        # as the generic one, never as is, and never breaks reading the session list.
        if fields["failure_message"] is not None:
            if fields["failure_message"] not in FAILURE_MESSAGES:
                fields["failure_message"] = GENERIC_FAILURE
                fields["failure_kind"] = FailureKind.GENERAL
            elif fields["failure_kind"] not in FAILURE_KINDS:
                fields["failure_kind"] = FailureKind.GENERAL
        elif fields["failure_kind"] is not None:
            fields["failure_kind"] = None
        return Session(**fields)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(self, title: str, source: SourceKind) -> Session:
        now = _now()
        sid = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO sessions (id, title, source, status, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (sid, title, source.value, SessionStatus.CREATED.value, now, now),
            )
        return self.get(sid)  # type: ignore[return-value]

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._session(row) if row else None

    def list(self) -> list[Session]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [self._session(r) for r in rows]

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
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id)
            )

    def fail(
        self,
        session_id: str,
        *,
        message: FailureMessage,
        kind: FailureKind,
        detail: str,
        stage: Stage | None = None,
    ) -> None:
        """Mark the session failed: ``message`` and ``kind`` for the user, ``detail`` (the raw
        error) for developers, and the stage it stopped at, if known."""
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
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE sessions SET {cols} WHERE id = ?", (*fields.values(), session_id)
            )

    def clear_error(self, session_id: str) -> None:
        """Forget a previous failure (a new run is starting)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE sessions SET failure_message = NULL, failure_kind = NULL,"
                " error_detail = NULL, failed_at = NULL, error = NULL WHERE id = ?",
                (session_id,),
            )

    def failure_detail(self, session_id: str) -> FailureDetail | None:
        """The raw error of the session's last failed run (developers only), or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT error_detail, failed_at FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None or row["error_detail"] is None:
            return None
        return FailureDetail(error=row["error_detail"], at=row["failed_at"])

    def delete(self, session_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
