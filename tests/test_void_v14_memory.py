import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from void_v14.memory import ExperimentalMemory
from void_v14.schemas import (
    BudgetUsage,
    ConflictLevel,
    ExperimentalResult,
    ExperimentalState,
)


def result(trace_id, synthesis="private synthesis"):
    return ExperimentalResult(
        trace_id=trace_id,
        state=ExperimentalState.COHERENT,
        agent_outputs=(),
        conflict_score=0.0,
        conflict_level=ConflictLevel.COHERENT,
        synthesis=synthesis,
        confidence=0.8,
        warnings=(),
        suggested_action="send_synthesis",
        budget_usage=BudgetUsage(10, 10, 0.01),
        rounds_used=0,
    )


class VoidV14MemoryTests(unittest.TestCase):
    def test_stable_database_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            stable = Path(root) / "void.db"
            with self.assertRaises(ValueError):
                ExperimentalMemory(stable, retention_days=30, forbidden_paths=(stable,))

    def test_user_trace_isolation_wal_and_metadata_only_storage(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "experimental.sqlite3"
            memory = ExperimentalMemory(path, retention_days=30)
            memory.record_result(1, result("shared", "user one raw synthesis"))
            memory.record_result(2, result("shared", "user two raw synthesis"))
            self.assertEqual(memory.get_trace(1, "shared")["user_id"], 1)
            self.assertEqual(memory.get_trace(2, "shared")["user_id"], 2)
            self.assertEqual(len(memory.list_traces(1)), 1)
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0].casefold(), "wal")
                columns = {row[1] for row in conn.execute("PRAGMA table_info(experimental_traces)")}
            self.assertNotIn("request", columns)
            self.assertNotIn("stable_context", columns)
            self.assertNotIn("synthesis", columns)

    def test_ttl_purge_removes_only_expired_metadata(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "experimental.sqlite3"
            memory = ExperimentalMemory(path, retention_days=30)
            memory.record_result(1, result("old"))
            memory.record_result(1, result("fresh"))
            past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute("UPDATE experimental_traces SET expires_at=? WHERE trace_id='old'", (past,))
            self.assertEqual(memory.purge_expired(), 1)
            self.assertIsNone(memory.get_trace(1, "old"))
            self.assertIsNotNone(memory.get_trace(1, "fresh"))


if __name__ == "__main__":
    unittest.main()
