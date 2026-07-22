"""Metadata-only SQLite memory for VOID v14 experiments."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .schemas import ExperimentalResult


logger = logging.getLogger("void_v14.memory")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExperimentalMemory:
    def __init__(
        self,
        path: Path,
        *,
        retention_days: int,
        forbidden_paths: Iterable[Path] = (),
    ) -> None:
        if isinstance(path, sqlite3.Connection):
            raise TypeError("experimental memory accepts a separate file path, not a connection")
        self.path = Path(path).expanduser().resolve()
        forbidden = {Path(item).expanduser().resolve() for item in forbidden_paths}
        if self.path in forbidden:
            raise ValueError("experimental memory cannot use the stable database")
        if retention_days <= 0:
            raise ValueError("retention_days must be positive")
        self.retention_days = retention_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experimental_traces (
                    trace_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    conflict_score REAL NOT NULL,
                    confidence REAL NOT NULL,
                    warnings_json TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    synthesis_sha256 TEXT NOT NULL,
                    rounds_used INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, trace_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_v14_trace_expiry "
                "ON experimental_traces(expires_at)"
            )

    def record_result(self, user_id: int, result: ExperimentalResult) -> None:
        now = _utc_now()
        expires = now + timedelta(days=self.retention_days)
        budget = result.budget_usage
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR REPLACE INTO experimental_traces(
                    trace_id, user_id, state, conflict_score, confidence,
                    warnings_json, budget_json, synthesis_sha256, rounds_used,
                    created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.trace_id,
                    int(user_id),
                    result.state.value,
                    float(result.conflict_score),
                    float(result.confidence),
                    json.dumps(list(result.warnings), ensure_ascii=False),
                    json.dumps(
                        {
                            "prompt_tokens": budget.prompt_tokens,
                            "completion_tokens": budget.completion_tokens,
                            "estimated_cost_usd": budget.estimated_cost_usd,
                        },
                        separators=(",", ":"),
                    ),
                    hashlib.sha256(result.synthesis.encode("utf-8")).hexdigest(),
                    int(result.rounds_used),
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
        logger.info("stored v14 trace metadata trace_id=%s user_id=%s state=%s", result.trace_id, user_id, result.state.value)

    def get_trace(self, user_id: int, trace_id: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT * FROM experimental_traces WHERE user_id=? AND trace_id=?",
                (int(user_id), str(trace_id)),
            ).fetchone()
        return dict(row) if row else None

    def list_traces(self, user_id: int, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM experimental_traces WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (int(user_id), max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def purge_expired(self, *, now: datetime | None = None) -> int:
        cutoff = (now or _utc_now()).astimezone(timezone.utc).isoformat()
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute("DELETE FROM experimental_traces WHERE expires_at <= ?", (cutoff,))
        return int(cursor.rowcount)
