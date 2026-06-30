import sqlite3
from datetime import datetime, timezone
from typing import Any

from config import settings


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT NOT NULL UNIQUE,
                source_name TEXT NOT NULL,
                published_at TEXT,
                frequency TEXT,
                score INTEGER DEFAULT 0,
                status TEXT DEFAULT 'NEW',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                rubric TEXT NOT NULL,
                title TEXT NOT NULL,
                post TEXT NOT NULL,
                source_name TEXT NOT NULL,
                source_url TEXT NOT NULL,
                frequency TEXT NOT NULL,
                publish_score INTEGER DEFAULT 0,
                status TEXT DEFAULT 'DRAFT',
                tg_message_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES news_candidates(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                draft_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(draft_id, user_id),
                FOREIGN KEY(draft_id) REFERENCES drafts(id)
            )
            """
        )
        conn.commit()


def upsert_candidate(item: dict[str, Any]) -> int | None:
    with get_connection() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO news_candidates
                (title, summary, url, source_name, published_at, frequency, score, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'NEW', ?)
                """,
                (
                    item["title"],
                    item.get("summary", ""),
                    item["url"],
                    item["source_name"],
                    item.get("published_at", ""),
                    item.get("frequency", "SIGNAL"),
                    item.get("score", 0),
                    now_iso(),
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute("SELECT id FROM news_candidates WHERE url = ?", (item["url"],)).fetchone()
            return int(row["id"]) if row else None


def list_candidates(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM news_candidates
            WHERE status IN ('NEW', 'DRAFTED')
            ORDER BY score DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_candidate(candidate_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM news_candidates WHERE id = ?", (candidate_id,)).fetchone()


def mark_candidate_status(candidate_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE news_candidates SET status = ? WHERE id = ?", (status, candidate_id))
        conn.commit()


def save_draft(draft: dict[str, Any]) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO drafts
            (candidate_id, rubric, title, post, source_name, source_url, frequency, publish_score,
             status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
            """,
            (
                draft["candidate_id"],
                draft["rubric"],
                draft["title"],
                draft["post"],
                draft["source_name"],
                draft["source_url"],
                draft["frequency"],
                draft.get("publish_score", 0),
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def list_drafts(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM drafts
            WHERE status = 'DRAFT'
            ORDER BY publish_score DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_draft(draft_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()


def mark_draft_published(draft_id: int, tg_message_id: int | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE drafts
            SET status = 'PUBLISHED', tg_message_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (tg_message_id, now_iso(), draft_id),
        )
        row = conn.execute("SELECT candidate_id FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row:
            conn.execute("UPDATE news_candidates SET status = 'PUBLISHED' WHERE id = ?", (row["candidate_id"],))
        conn.commit()


def save_catch(draft_id: int, user_id: int, username: str | None, first_name: str | None) -> bool:
    with get_connection() as conn:
        try:
            conn.execute(
                """
                INSERT INTO catches (draft_id, user_id, username, first_name, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (draft_id, user_id, username, first_name, now_iso()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_stats() -> dict[str, int]:
    with get_connection() as conn:
        candidates = conn.execute("SELECT COUNT(*) AS c FROM news_candidates").fetchone()["c"]
        drafts = conn.execute("SELECT COUNT(*) AS c FROM drafts").fetchone()["c"]
        published = conn.execute("SELECT COUNT(*) AS c FROM drafts WHERE status = 'PUBLISHED'").fetchone()["c"]
        catches = conn.execute("SELECT COUNT(*) AS c FROM catches").fetchone()["c"]
        return {
            "candidates": int(candidates),
            "drafts": int(drafts),
            "published": int(published),
            "catches": int(catches),
        }
