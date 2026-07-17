"""Small SQLite state store for authoritative Voice Hub session ownership."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ACTIVE_STATES = ("minting", "active", "attached", "finalizing")


@dataclass(frozen=True)
class StoredSession:
    session_id: str
    platform: str
    user_id: str
    persona: str
    started_at: int
    expires_at: int
    state: str
    realtime_session_id: str | None
    call_id: str | None
    summary: str | None
    summary_receipt: str | None


class StoreConflictError(RuntimeError):
    pass


class VoiceHubStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self):
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    session_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    persona TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    realtime_session_id TEXT,
                    call_id TEXT,
                    summary TEXT,
                    summary_receipt TEXT,
                    usage_json TEXT NOT NULL DEFAULT '{}',
                    lifecycle_json TEXT NOT NULL DEFAULT '{}',
                    finish_reason TEXT,
                    finished_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS voice_sessions_one_active_user
                ON voice_sessions(platform, user_id)
                WHERE state IN ('minting', 'active', 'attached', 'finalizing');
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> StoredSession | None:
        if row is None:
            return None
        return StoredSession(
            session_id=row["session_id"],
            platform=row["platform"],
            user_id=row["user_id"],
            persona=row["persona"],
            started_at=row["started_at"],
            expires_at=row["expires_at"],
            state=row["state"],
            realtime_session_id=row["realtime_session_id"],
            call_id=row["call_id"],
            summary=row["summary"],
            summary_receipt=row["summary_receipt"],
        )

    def reserve(
        self,
        *,
        session_id: str,
        platform: str,
        user_id: str,
        persona: str,
        duration_seconds: int,
    ) -> StoredSession:
        now = int(self._clock())
        with self._db() as conn:
            conn.execute(
                """
                UPDATE voice_sessions SET state='expired', finished_at=?
                WHERE state IN ('minting','active') AND expires_at <= ?
                """,
                (now, now),
            )
            try:
                conn.execute(
                    """
                    INSERT INTO voice_sessions(
                        session_id, platform, user_id, persona, started_at, expires_at, state
                    ) VALUES (?, ?, ?, ?, ?, ?, 'minting')
                    """,
                    (session_id, platform, user_id, persona, now, now + duration_seconds),
                )
            except sqlite3.IntegrityError as exc:
                raise StoreConflictError("User already has an active session") from exc
        session = self.get(session_id)
        assert session is not None
        return session

    def activate(self, session_id: str, realtime_session_id: str) -> StoredSession:
        with self._db() as conn:
            cursor = conn.execute(
                """
                UPDATE voice_sessions SET state='active', realtime_session_id=?
                WHERE session_id=? AND state='minting'
                """,
                (realtime_session_id, session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
        session = self.get(session_id)
        assert session is not None
        return session

    def attach(self, session_id: str, call_id: str) -> StoredSession:
        with self._db() as conn:
            row = conn.execute(
                "SELECT call_id FROM voice_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise KeyError(session_id)
            if row["call_id"] and row["call_id"] != call_id:
                raise StoreConflictError("Session is already attached to another call")
            conn.execute(
                "UPDATE voice_sessions SET state='attached', call_id=? WHERE session_id=?",
                (call_id, session_id),
            )
        session = self.get(session_id)
        assert session is not None
        return session

    def begin_finalization(self, session_id: str) -> StoredSession:
        with self._db() as conn:
            conn.execute(
                """
                UPDATE voice_sessions SET state='finalizing'
                WHERE session_id=? AND state IN ('active','attached','expired','finalizing')
                """,
                (session_id,),
            )
        session = self.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def save_server_summary(self, session_id: str, summary: str) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE voice_sessions SET summary=? WHERE session_id=? AND summary IS NULL",
                (summary, session_id),
            )

    def finish(
        self,
        session_id: str,
        *,
        receipt: str,
        reason: str,
        usage: dict[str, Any],
        lifecycle: dict[str, Any],
    ) -> StoredSession:
        now = int(self._clock())
        with self._db() as conn:
            conn.execute(
                """
                UPDATE voice_sessions
                SET state='finished', summary_receipt=?, finish_reason=?, finished_at=?,
                    usage_json=?, lifecycle_json=?
                WHERE session_id=?
                """,
                (
                    receipt,
                    reason,
                    now,
                    json.dumps(usage, separators=(",", ":"), sort_keys=True),
                    json.dumps(lifecycle, separators=(",", ":"), sort_keys=True),
                    session_id,
                ),
            )
        session = self.get(session_id)
        assert session is not None
        return session

    def abandon(self, session_id: str, reason: str) -> None:
        with self._db() as conn:
            conn.execute(
                """
                UPDATE voice_sessions SET state='abandoned', finish_reason=?, finished_at=?
                WHERE session_id=?
                """,
                (reason, int(self._clock()), session_id),
            )

    def delete_reservation(self, session_id: str) -> None:
        with self._db() as conn:
            conn.execute(
                "DELETE FROM voice_sessions WHERE session_id=? AND state='minting'",
                (session_id,),
            )

    def get(self, session_id: str) -> StoredSession | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM voice_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._row(row)

    def recoverable(self) -> list[StoredSession]:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT * FROM voice_sessions
                WHERE state IN ('minting','active','attached','finalizing','expired')
                ORDER BY started_at
                """
            ).fetchall()
        return [session for row in rows if (session := self._row(row)) is not None]
