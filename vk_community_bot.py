"""Durable VOID adapter for one allowlisted VK community.

The adapter uses VK Bots Long Poll, so it needs no public inbound HTTP endpoint.
Its API client exposes only ``groups.getLongPollServer``, ``messages.send``, and
``wall.createComment``; wall publication is intentionally impossible from this
process.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import socket
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlparse

import aiohttp

from void_dialog_adapter import DialogSettings, VoidDialogEngine


DEFAULT_GROUP_ID = 237593988
MAX_CACHED_POST_CONTEXTS = 500


def _positive_int(name: str, value: str, *, minimum: int = 1) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if result < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return result


def _csv_ints(name: str, raw: str, *, allow_empty: bool = False) -> frozenset[int]:
    values = frozenset(
        _positive_int(name, item.strip())
        for item in raw.split(",")
        if item.strip()
    )
    if not values and not allow_empty:
        raise ValueError(f"{name} must not be empty")
    return values


@dataclass(frozen=True, slots=True)
class Settings:
    enabled: bool
    group_id: int
    allowed_group_ids: frozenset[int]
    allowed_user_ids: frozenset[int]
    public_replies_enabled: bool
    token: str
    api_version: str
    state_db_path: Path
    health_path: Path
    long_poll_wait_seconds: int
    http_timeout_seconds: int
    rate_limit_count: int
    rate_limit_window_seconds: int
    max_text_chars: int
    max_post_context_chars: int
    max_reply_chars: int
    max_public_reply_chars: int
    max_attempts: int
    retry_base_seconds: int
    processing_lease_seconds: int

    @classmethod
    def from_env(cls, *, require_token: bool = True) -> "Settings":
        enabled = os.getenv("VK_COMMUNITY_BOT_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if require_token and not enabled:
            raise ValueError("VK_COMMUNITY_BOT_ENABLED must be true")
        group_id = _positive_int(
            "VK_COMMUNITY_BOT_GROUP_ID",
            os.getenv(
                "VK_COMMUNITY_BOT_GROUP_ID",
                os.getenv("VK_GROUP_ID", str(DEFAULT_GROUP_ID)),
            ),
        )
        allowed_group_ids = _csv_ints(
            "VK_COMMUNITY_ALLOWED_GROUP_IDS",
            os.getenv("VK_COMMUNITY_ALLOWED_GROUP_IDS", str(DEFAULT_GROUP_ID)),
        )
        if group_id not in allowed_group_ids or len(allowed_group_ids) != 1:
            raise ValueError(
                "VK community bot must target exactly one explicitly allowlisted group"
            )
        token = os.getenv("VK_GROUP_ACCESS_TOKEN", "").strip()
        if require_token and not token:
            raise ValueError("VK_GROUP_ACCESS_TOKEN is required")
        return cls(
            enabled=enabled,
            group_id=group_id,
            allowed_group_ids=allowed_group_ids,
            allowed_user_ids=_csv_ints(
                "VK_COMMUNITY_ALLOWED_USER_IDS",
                os.getenv("VK_COMMUNITY_ALLOWED_USER_IDS", ""),
                allow_empty=True,
            ),
            public_replies_enabled=os.getenv(
                "VK_COMMUNITY_PUBLIC_REPLIES_ENABLED", "false"
            ).strip().lower()
            in {"1", "true", "yes", "on"},
            token=token,
            api_version=os.getenv("VK_API_VERSION", "5.199").strip() or "5.199",
            state_db_path=Path(
                os.getenv(
                    "VK_COMMUNITY_STATE_DB",
                    "/var/lib/void-vk-community/events.sqlite3",
                )
            ),
            health_path=Path(
                os.getenv(
                    "VK_COMMUNITY_HEALTH_FILE",
                    "/var/lib/void-vk-community/status.json",
                )
            ),
            long_poll_wait_seconds=min(
                _positive_int(
                    "VK_COMMUNITY_LONG_POLL_WAIT_SECONDS",
                    os.getenv("VK_COMMUNITY_LONG_POLL_WAIT_SECONDS", "25"),
                ),
                45,
            ),
            http_timeout_seconds=min(
                _positive_int(
                    "VK_COMMUNITY_HTTP_TIMEOUT_SECONDS",
                    os.getenv("VK_COMMUNITY_HTTP_TIMEOUT_SECONDS", "40"),
                ),
                90,
            ),
            rate_limit_count=min(
                _positive_int(
                    "VK_COMMUNITY_USER_RATE_LIMIT",
                    os.getenv("VK_COMMUNITY_USER_RATE_LIMIT", "6"),
                ),
                100,
            ),
            rate_limit_window_seconds=min(
                _positive_int(
                    "VK_COMMUNITY_USER_RATE_WINDOW_SECONDS",
                    os.getenv("VK_COMMUNITY_USER_RATE_WINDOW_SECONDS", "60"),
                ),
                3600,
            ),
            max_text_chars=min(
                _positive_int(
                    "VK_COMMUNITY_MAX_TEXT_CHARS",
                    os.getenv("VK_COMMUNITY_MAX_TEXT_CHARS", "4000"),
                ),
                12000,
            ),
            max_post_context_chars=min(
                _positive_int(
                    "VK_COMMUNITY_MAX_POST_CONTEXT_CHARS",
                    os.getenv("VK_COMMUNITY_MAX_POST_CONTEXT_CHARS", "12000"),
                ),
                16000,
            ),
            max_reply_chars=min(
                _positive_int(
                    "VK_COMMUNITY_MAX_REPLY_CHARS",
                    os.getenv("VK_COMMUNITY_MAX_REPLY_CHARS", "3500"),
                ),
                4000,
            ),
            max_public_reply_chars=min(
                _positive_int(
                    "VK_COMMUNITY_MAX_PUBLIC_REPLY_CHARS",
                    os.getenv("VK_COMMUNITY_MAX_PUBLIC_REPLY_CHARS", "900"),
                ),
                2000,
            ),
            max_attempts=min(
                _positive_int(
                    "VK_COMMUNITY_MAX_ATTEMPTS",
                    os.getenv("VK_COMMUNITY_MAX_ATTEMPTS", "5"),
                ),
                20,
            ),
            retry_base_seconds=min(
                _positive_int(
                    "VK_COMMUNITY_RETRY_BASE_SECONDS",
                    os.getenv("VK_COMMUNITY_RETRY_BASE_SECONDS", "5"),
                ),
                300,
            ),
            processing_lease_seconds=max(
                60,
                min(
                    _positive_int(
                        "VK_COMMUNITY_PROCESSING_LEASE_SECONDS",
                        os.getenv("VK_COMMUNITY_PROCESSING_LEASE_SECONDS", "300"),
                    ),
                    3600,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class InboundMessage:
    event_id: str
    group_id: int
    user_id: int
    peer_id: int
    text: str
    raw: Mapping[str, Any]
    event_kind: str = "private_message"
    owner_id: int = 0
    post_id: int = 0
    comment_id: int = 0


@dataclass(frozen=True, slots=True)
class QueuedEvent:
    event_id: str
    user_id: int
    text: str
    response_text: str
    attempts: int
    event_kind: str
    owner_id: int
    post_id: int
    comment_id: int


@dataclass(frozen=True, slots=True)
class WallPostContext:
    owner_id: int
    post_id: int
    text: str


def _fallback_event_id(update: Mapping[str, Any]) -> str:
    canonical = json.dumps(update, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_message(
    update: Mapping[str, Any], settings: Settings
) -> InboundMessage | None:
    if update.get("type") != "message_new":
        return None
    try:
        group_id = int(update.get("group_id"))
    except (TypeError, ValueError):
        return None
    if group_id != settings.group_id or group_id not in settings.allowed_group_ids:
        return None
    container = update.get("object")
    message = container.get("message") if isinstance(container, Mapping) else None
    if not isinstance(message, Mapping):
        return None
    try:
        user_id = int(message.get("from_id"))
        peer_id = int(message.get("peer_id"))
        outgoing = int(message.get("out", 0))
    except (TypeError, ValueError):
        return None
    # Only private inbound messages from real users. Chats and community messages
    # are outside this adapter's authority.
    if outgoing != 0 or user_id <= 0 or peer_id != user_id:
        return None
    if settings.allowed_user_ids and user_id not in settings.allowed_user_ids:
        return None
    text = str(message.get("text") or "").replace("\x00", "").strip()
    if not text:
        return None
    text = text[: settings.max_text_chars]
    event_id = str(update.get("event_id") or "").strip() or _fallback_event_id(update)
    return InboundMessage(event_id, group_id, user_id, peer_id, text, update)


PUBLIC_INVOCATIONS = frozenset(
    {"void", "войд", "войда", "сущность", "entity"}
)


def _public_reply_requested(text: str) -> bool:
    tokens = frozenset(re.findall(r"[0-9a-zа-яё]+", text.casefold()))
    return "?" in text or bool(tokens & PUBLIC_INVOCATIONS)


def _direct_public_invocation(text: str) -> bool:
    tokens = frozenset(re.findall(r"[0-9a-zа-яё]+", text.casefold()))
    return bool(tokens & PUBLIC_INVOCATIONS)


def _community_comment_actor(
    item: Mapping[str, Any], settings: Settings
) -> int | None:
    """Resolve a human actor for a comment published as the community.

    VK commonly exposes only the community's negative ``from_id`` for these
    comments. A positive signer is preferred when present. During a one-user
    pilot, the sole allowlisted user is the only possible administrator actor.
    Open installations use the positive group id as a separate public-dialog
    identity rather than mixing the thread with a private user conversation.
    """
    try:
        signer_id = int(item.get("signer_id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if signer_id > 0:
        if settings.allowed_user_ids and signer_id not in settings.allowed_user_ids:
            return None
        return signer_id
    if len(settings.allowed_user_ids) == 1:
        return next(iter(settings.allowed_user_ids))
    if not settings.allowed_user_ids:
        return settings.group_id
    return None


def normalize_wall_activity(
    update: Mapping[str, Any], settings: Settings
) -> InboundMessage | None:
    """Accept only direct public prompts on this community's own wall.

    Community-authored posts and comments have a negative ``from_id`` and are
    rejected before generation, preventing the bot from consuming its own
    output. Plain public chatter is ignored unless it contains a question or a
    direct VOID invocation.
    """
    if not settings.public_replies_enabled:
        return None
    event_type = str(update.get("type") or "")
    if event_type not in {"wall_reply_new", "wall_post_new"}:
        return None
    try:
        group_id = int(update.get("group_id"))
    except (TypeError, ValueError):
        return None
    if group_id != settings.group_id or group_id not in settings.allowed_group_ids:
        return None
    raw_object = update.get("object")
    if not isinstance(raw_object, Mapping):
        return None
    nested_key = "comment" if event_type == "wall_reply_new" else "post"
    nested = raw_object.get(nested_key)
    item = nested if isinstance(nested, Mapping) else raw_object
    try:
        user_id = int(item.get("from_id"))
        owner_id = int(item.get("owner_id", -group_id))
        post_id = int(
            item.get("post_id")
            if event_type == "wall_reply_new"
            else item.get("id")
        )
        comment_id = int(item.get("id", 0)) if event_type == "wall_reply_new" else 0
    except (TypeError, ValueError):
        return None
    if owner_id != -group_id or post_id <= 0:
        return None
    if event_type == "wall_reply_new" and comment_id <= 0:
        return None
    text = str(item.get("text") or "").replace("\x00", "").strip()
    if not text or not _public_reply_requested(text):
        return None

    if user_id <= 0:
        # A human administrator can publish a wall comment as the community.
        # Accept only direct invocations, never community-authored posts, and
        # reject VOID's own prefixed replies so Long Poll cannot create a loop.
        if (
            event_type != "wall_reply_new"
            or user_id != -group_id
            or not _direct_public_invocation(text)
            or re.match(r"^\s*void\s*//", text, flags=re.IGNORECASE)
        ):
            return None
        actor_id = _community_comment_actor(item, settings)
        if actor_id is None:
            return None
        user_id = actor_id
    elif settings.allowed_user_ids and user_id not in settings.allowed_user_ids:
        return None
    event_id = str(update.get("event_id") or "").strip() or _fallback_event_id(update)
    return InboundMessage(
        event_id=event_id,
        group_id=group_id,
        user_id=user_id,
        peer_id=0,
        text=text[: settings.max_text_chars],
        raw=update,
        event_kind=(
            "wall_comment" if event_type == "wall_reply_new" else "wall_post"
        ),
        owner_id=owner_id,
        post_id=post_id,
        comment_id=comment_id,
    )


def normalize_wall_post_context(
    update: Mapping[str, Any], settings: Settings
) -> WallPostContext | None:
    """Extract only community-authored text from a post-created event."""
    if update.get("type") != "wall_post_new":
        return None
    try:
        group_id = int(update.get("group_id"))
    except (TypeError, ValueError):
        return None
    if group_id != settings.group_id or group_id not in settings.allowed_group_ids:
        return None
    raw_object = update.get("object")
    if not isinstance(raw_object, Mapping):
        return None
    nested = raw_object.get("post")
    item = nested if isinstance(nested, Mapping) else raw_object
    try:
        owner_id = int(item.get("owner_id", -group_id))
        post_id = int(item.get("id"))
        from_id = int(item.get("from_id"))
    except (TypeError, ValueError):
        return None
    if owner_id != -group_id or from_id != -group_id or post_id <= 0:
        return None
    text = str(item.get("text") or "").replace("\x00", "").strip()
    if not text:
        return None
    return WallPostContext(
        owner_id=owner_id,
        post_id=post_id,
        text=text[: settings.max_post_context_chars],
    )


class EventStore:
    """Durable inbox, dedupe ledger, retry state and per-user rate windows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init_schema(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vk_community_events (
                    event_id TEXT PRIMARY KEY,
                    group_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    raw_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    response_text TEXT NOT NULL DEFAULT '',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    received_at REAL NOT NULL,
                    claimed_at REAL,
                    next_attempt_at REAL NOT NULL,
                    completed_at REAL,
                    last_error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_vk_community_events_work
                ON vk_community_events(status, next_attempt_at, received_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vk_community_rate_limits (
                    user_id INTEGER PRIMARY KEY,
                    window_started_at REAL NOT NULL,
                    message_count INTEGER NOT NULL,
                    notice_sent INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vk_community_post_contexts (
                    owner_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    observed_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, post_id)
                )
                """
            )
            rate_columns = {
                str(row[1])
                for row in connection.execute(
                    "PRAGMA table_info(vk_community_rate_limits)"
                ).fetchall()
            }
            if "notice_sent" not in rate_columns:
                connection.execute(
                    "ALTER TABLE vk_community_rate_limits "
                    "ADD COLUMN notice_sent INTEGER NOT NULL DEFAULT 0"
                )

    def remember_post_contexts(
        self,
        posts: Sequence[WallPostContext],
        *,
        now: float | None = None,
    ) -> int:
        timestamp = time.time() if now is None else now
        stored = 0
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for post in posts:
                connection.execute(
                    """
                    INSERT INTO vk_community_post_contexts(
                        owner_id, post_id, text, observed_at
                    ) VALUES(?, ?, ?, ?)
                    ON CONFLICT(owner_id, post_id) DO UPDATE SET
                        text=excluded.text,
                        observed_at=excluded.observed_at
                    """,
                    (post.owner_id, post.post_id, post.text, timestamp),
                )
                stored += 1
            if posts:
                connection.execute(
                    """
                    DELETE FROM vk_community_post_contexts
                    WHERE (owner_id, post_id) NOT IN (
                        SELECT owner_id, post_id
                        FROM vk_community_post_contexts
                        ORDER BY observed_at DESC, owner_id, post_id
                        LIMIT ?
                    )
                    """,
                    (MAX_CACHED_POST_CONTEXTS,),
                )
        return stored

    def post_context(self, owner_id: int, post_id: int) -> str:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                """
                SELECT text
                FROM vk_community_post_contexts
                WHERE owner_id=? AND post_id=?
                """,
                (owner_id, post_id),
            ).fetchone()
        return str(row["text"]) if row else ""

    def ingest(self, messages: Sequence[InboundMessage], *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        inserted = 0
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            for message in messages:
                routing = {
                    "event_id": message.event_id,
                    "group_id": message.group_id,
                    "user_id": message.user_id,
                    "peer_id": message.peer_id,
                }
                if message.event_kind != "private_message":
                    routing.update(
                        {
                            "event_kind": message.event_kind,
                            "owner_id": message.owner_id,
                            "post_id": message.post_id,
                            "comment_id": message.comment_id,
                        }
                    )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO vk_community_events(
                        event_id, group_id, user_id, text, raw_json,
                        received_at, next_attempt_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message.event_id,
                        message.group_id,
                        message.user_id,
                        message.text,
                        # Keep only the routing evidence required for an audit.
                        # Full Long Poll payloads may contain unrelated profile or
                        # attachment metadata and do not belong in this inbox.
                        json.dumps(routing, ensure_ascii=False, sort_keys=True),
                        timestamp,
                        timestamp,
                    ),
                )
                inserted += cursor.rowcount
        return inserted

    def claim_next(
        self, *, now: float | None = None, lease_seconds: int = 300
    ) -> QueuedEvent | None:
        timestamp = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE vk_community_events
                SET status=CASE WHEN response_text='' THEN 'pending' ELSE 'ready' END,
                    claimed_at=NULL
                WHERE status='processing' AND claimed_at < ?
                """,
                (timestamp - lease_seconds,),
            )
            row = connection.execute(
                """
                SELECT event_id, user_id, text, raw_json, response_text, attempts
                FROM vk_community_events
                WHERE status IN ('pending', 'ready') AND next_attempt_at <= ?
                ORDER BY received_at, event_id
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE vk_community_events
                SET status='processing', claimed_at=?
                WHERE event_id=? AND status IN ('pending', 'ready')
                """,
                (timestamp, row["event_id"]),
            )
            if updated.rowcount != 1:
                return None
        try:
            routing = json.loads(str(row["raw_json"]))
        except (json.JSONDecodeError, TypeError) as exc:
            raise RuntimeError("stored VK event routing is invalid") from exc
        if not isinstance(routing, Mapping):
            raise RuntimeError("stored VK event routing is invalid")
        event_kind = str(routing.get("event_kind") or "private_message")
        if event_kind not in {"private_message", "wall_comment", "wall_post"}:
            raise RuntimeError("stored VK event kind is invalid")
        return QueuedEvent(
            event_id=str(row["event_id"]),
            user_id=int(row["user_id"]),
            text=str(row["text"]),
            response_text=str(row["response_text"] or ""),
            attempts=int(row["attempts"]),
            event_kind=event_kind,
            owner_id=int(routing.get("owner_id") or 0),
            post_id=int(routing.get("post_id") or 0),
            comment_id=int(routing.get("comment_id") or 0),
        )

    def save_response(self, event_id: str, response_text: str) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE vk_community_events
                SET response_text=?, status='processing'
                WHERE event_id=? AND status='processing'
                """,
                (response_text, event_id),
            )

    def complete(self, event_id: str, *, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                UPDATE vk_community_events
                SET status='done', completed_at=?, claimed_at=NULL, last_error=''
                WHERE event_id=? AND status='processing'
                """,
                (timestamp, event_id),
            )

    def fail(
        self,
        event_id: str,
        error_kind: str,
        *,
        max_attempts: int,
        retry_base_seconds: int,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempts, response_text FROM vk_community_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None:
                return "missing"
            attempts = int(row["attempts"]) + 1
            status = "dead" if attempts >= max_attempts else (
                "ready" if row["response_text"] else "pending"
            )
            delay = retry_base_seconds * (2 ** min(attempts - 1, 6))
            connection.execute(
                """
                UPDATE vk_community_events
                SET status=?, attempts=?, claimed_at=NULL, next_attempt_at=?, last_error=?
                WHERE event_id=?
                """,
                (status, attempts, timestamp + delay, error_kind[:120], event_id),
            )
        return status

    def rate_decision(
        self,
        user_id: int,
        *,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT window_started_at, message_count, notice_sent
                FROM vk_community_rate_limits
                WHERE user_id=?
                """,
                (user_id,),
            ).fetchone()
            if row is None or float(row["window_started_at"]) <= timestamp - window_seconds:
                connection.execute(
                    """
                    INSERT INTO vk_community_rate_limits(
                        user_id, window_started_at, message_count, notice_sent
                    ) VALUES(?, ?, 1, 0)
                    ON CONFLICT(user_id) DO UPDATE SET
                        window_started_at=excluded.window_started_at,
                        message_count=1,
                        notice_sent=0
                    """,
                    (user_id, timestamp),
                )
                return "allow"
            count = int(row["message_count"])
            if count >= limit:
                if not int(row["notice_sent"]):
                    connection.execute(
                        "UPDATE vk_community_rate_limits SET notice_sent=1 WHERE user_id=?",
                        (user_id,),
                    )
                    return "notify"
                return "drop"
            connection.execute(
                "UPDATE vk_community_rate_limits SET message_count=message_count+1 WHERE user_id=?",
                (user_id,),
            )
            return "allow"

    def counts(self) -> dict[str, int]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS total FROM vk_community_events GROUP BY status"
            ).fetchall()
        result = {"pending": 0, "ready": 0, "processing": 0, "done": 0, "dead": 0}
        result.update({str(row["status"]): int(row["total"]) for row in rows})
        return result


class VkTransport(Protocol):
    async def get_long_poll_server(self) -> tuple[str, str, str]: ...

    async def poll(self, server: str, key: str, ts: str) -> Mapping[str, Any]: ...

    async def send_message(self, user_id: int, text: str, random_id: int) -> int: ...

    async def send_wall_comment(
        self,
        owner_id: int,
        post_id: int,
        reply_to_comment: int,
        text: str,
        guid: str,
    ) -> int: ...


class VkApiClient:
    """Narrow VK API client with no wall publication or deletion methods."""

    _API_ROOT = "https://api.vk.com/method"

    def __init__(self, settings: Settings, session: aiohttp.ClientSession) -> None:
        self.settings = settings
        self.session = session
        self._send_lock = asyncio.Lock()
        self._last_send_at = 0.0

    async def _api(self, method: str, params: Mapping[str, Any]) -> Any:
        if method not in {
            "groups.getLongPollServer",
            "messages.send",
            "wall.createComment",
        }:
            raise ValueError("VK API method is not allowlisted")
        payload = dict(params)
        payload.update({"access_token": self.settings.token, "v": self.settings.api_version})
        async with self.session.post(f"{self._API_ROOT}/{method}", data=payload) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        if not isinstance(body, Mapping):
            raise RuntimeError("VK API returned a malformed response")
        if "error" in body:
            error = body.get("error")
            code = error.get("error_code") if isinstance(error, Mapping) else "unknown"
            raise RuntimeError(f"VK API error {code}")
        return body.get("response")

    async def get_long_poll_server(self) -> tuple[str, str, str]:
        response = await self._api(
            "groups.getLongPollServer", {"group_id": self.settings.group_id}
        )
        if not isinstance(response, Mapping):
            raise RuntimeError("VK Long Poll credentials are missing")
        server = str(response.get("server") or "")
        parsed = urlparse(server)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not (host == "vk.com" or host.endswith(".vk.com"))
        ):
            raise RuntimeError("VK returned an untrusted Long Poll server")
        key = str(response.get("key") or "")
        ts = str(response.get("ts") or "")
        if not key or not ts:
            raise RuntimeError("VK Long Poll key or timestamp is missing")
        return server, key, ts

    async def poll(self, server: str, key: str, ts: str) -> Mapping[str, Any]:
        params = {
            "act": "a_check",
            "key": key,
            "ts": ts,
            "wait": self.settings.long_poll_wait_seconds,
        }
        async with self.session.get(server, params=params) as response:
            response.raise_for_status()
            body = await response.json(content_type=None)
        if not isinstance(body, Mapping):
            raise RuntimeError("VK Long Poll returned a malformed response")
        return body

    async def send_message(self, user_id: int, text: str, random_id: int) -> int:
        # VK permits more, but keeping writes below three requests/second gives the
        # service a conservative global outbound ceiling.
        async with self._send_lock:
            delay = 0.36 - (time.monotonic() - self._last_send_at)
            if delay > 0:
                await asyncio.sleep(delay)
            response = await self._api(
                "messages.send",
                {"peer_id": user_id, "random_id": random_id, "message": text},
            )
            self._last_send_at = time.monotonic()
        if not isinstance(response, int):
            raise RuntimeError("VK messages.send returned no message id")
        return response

    async def send_wall_comment(
        self,
        owner_id: int,
        post_id: int,
        reply_to_comment: int,
        text: str,
        guid: str,
    ) -> int:
        if owner_id != -self.settings.group_id or post_id <= 0:
            raise ValueError("VK wall comment target is not allowlisted")
        params: dict[str, Any] = {
            "owner_id": owner_id,
            "post_id": post_id,
            "from_group": self.settings.group_id,
            "message": text,
            "guid": guid,
        }
        if reply_to_comment > 0:
            params["reply_to_comment"] = reply_to_comment
        async with self._send_lock:
            delay = 0.36 - (time.monotonic() - self._last_send_at)
            if delay > 0:
                await asyncio.sleep(delay)
            response = await self._api("wall.createComment", params)
            self._last_send_at = time.monotonic()
        comment_id = (
            response.get("comment_id") if isinstance(response, Mapping) else response
        )
        if not isinstance(comment_id, int) or comment_id <= 0:
            raise RuntimeError("VK wall.createComment returned no comment id")
        return comment_id


class SystemdNotifier:
    def __init__(self) -> None:
        self.address = os.getenv("NOTIFY_SOCKET", "")

    def send(self, payload: str) -> None:
        if not self.address:
            return
        address = self.address
        if address.startswith("@"):
            address = "\0" + address[1:]
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
                client.connect(address)
                client.sendall(payload.encode("utf-8"))
        except OSError:
            pass


class CommunityBot:
    def __init__(
        self,
        settings: Settings,
        store: EventStore,
        generate: Callable[[int, str, str, str], Awaitable[str]],
        *,
        transport: VkTransport | None = None,
        notifier: SystemdNotifier | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.generate = generate
        self.transport = transport
        self.notifier = notifier or SystemdNotifier()
        self.stop_event = asyncio.Event()
        self.started_at = time.time()
        self.last_poll_at = 0.0
        self.last_event_at = 0.0
        self.consecutive_poll_failures = 0
        self.lifecycle = "starting"

    @staticmethod
    def random_id(event_id: str) -> int:
        value = int.from_bytes(hashlib.sha256(event_id.encode("utf-8")).digest()[:4], "big")
        return (value & 0x7FFFFFFF) or 1

    @staticmethod
    def comment_guid(event_id: str) -> str:
        return hashlib.sha256(
            ("void-vk-comment:" + event_id).encode("utf-8")
        ).hexdigest()[:32]

    def ingest_updates(self, updates: Sequence[Mapping[str, Any]]) -> int:
        accepted: list[InboundMessage] = []
        post_contexts: list[WallPostContext] = []
        for update in updates:
            post_context = normalize_wall_post_context(update, self.settings)
            if post_context is not None:
                post_contexts.append(post_context)
            event = normalize_message(update, self.settings)
            if event is None:
                event = normalize_wall_activity(update, self.settings)
            if event is not None:
                accepted.append(event)
        self.store.remember_post_contexts(post_contexts)
        return self.store.ingest(accepted)

    async def process_one(self) -> bool:
        event = self.store.claim_next(
            lease_seconds=self.settings.processing_lease_seconds
        )
        if event is None:
            return False
        try:
            response_text = event.response_text
            if not response_text:
                rate_decision = self.store.rate_decision(
                    event.user_id,
                    limit=self.settings.rate_limit_count,
                    window_seconds=self.settings.rate_limit_window_seconds,
                )
                if rate_decision == "allow":
                    post_context = ""
                    if event.event_kind == "wall_post":
                        post_context = event.text
                    elif event.event_kind == "wall_comment":
                        post_context = self.store.post_context(
                            event.owner_id, event.post_id
                        )
                    # VK dialogue history lives in its own database, so the native
                    # VK user id is safe and never overlaps Telegram memory.
                    response_text = await self.generate(
                        event.user_id,
                        event.text,
                        event.event_kind,
                        post_context,
                    )
                elif rate_decision == "notify":
                    if event.event_kind != "private_message":
                        self.store.complete(event.event_id)
                        self.last_event_at = time.time()
                        self.write_health("running")
                        return True
                    response_text = (
                        "Я рядом, но сообщений слишком много. "
                        "Дай мне минуту и продолжим."
                    )
                else:
                    # Only one rate-limit notice is sent per window; later
                    # excess events are handled without an outbound flood.
                    self.store.complete(event.event_id)
                    self.last_event_at = time.time()
                    self.write_health("running")
                    return True
                response_text = (response_text or "").replace("\x00", "").strip()
                if not response_text:
                    response_text = "Не успел сформулировать ответ. Попробуй ещё раз."
                if event.event_kind == "private_message":
                    response_text = response_text[: self.settings.max_reply_chars]
                else:
                    response_text = (
                        "VOID // " + response_text
                    )[: self.settings.max_public_reply_chars]
                self.store.save_response(event.event_id, response_text)
            if self.transport is None:  # pragma: no cover - run() always supplies it
                raise RuntimeError("VK transport is unavailable")
            if event.event_kind == "private_message":
                await self.transport.send_message(
                    event.user_id,
                    response_text,
                    self.random_id(event.event_id),
                )
            else:
                await self.transport.send_wall_comment(
                    event.owner_id,
                    event.post_id,
                    # VK hides replies addressed through reply_to_comment in a
                    # collapsed thread. Publish a root-level signed comment so
                    # the entity is immediately visible below the post.
                    0,
                    response_text,
                    self.comment_guid(event.event_id),
                )
            self.store.complete(event.event_id)
            self.last_event_at = time.time()
            self.write_health("running")
        except Exception as exc:
            state = self.store.fail(
                event.event_id,
                type(exc).__name__,
                max_attempts=self.settings.max_attempts,
                retry_base_seconds=self.settings.retry_base_seconds,
            )
            self.write_health("degraded" if state == "dead" else "running")
        return True

    def health(self, lifecycle: str | None = None) -> dict[str, Any]:
        counts = self.store.counts()
        effective_lifecycle = lifecycle or self.lifecycle
        if effective_lifecycle == "running" and counts.get("dead", 0):
            effective_lifecycle = "degraded"
        return {
            "schema": "void.vk-community-health.v1",
            "lifecycle": effective_lifecycle,
            "group_id": self.settings.group_id,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "updated_at": time.time(),
            "last_poll_at": self.last_poll_at,
            "last_event_at": self.last_event_at,
            "consecutive_poll_failures": self.consecutive_poll_failures,
            "queue": counts,
        }

    def write_health(self, lifecycle: str | None = None) -> None:
        self.settings.health_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.health(lifecycle)
        temporary = self.settings.health_path.with_suffix(
            self.settings.health_path.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o640)
        os.replace(temporary, self.settings.health_path)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for handled_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(handled_signal, self.stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass
        self.write_health("starting")
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if self.transport is None:
                self.transport = VkApiClient(self.settings, session)
            server = key = ts = ""
            server, key, ts = await self.transport.get_long_poll_server()
            self.lifecycle = "running"
            self.write_health()
            self.notifier.send("READY=1\nSTATUS=VK community Long Poll is running")
            while not self.stop_event.is_set():
                if await self.process_one():
                    self.notifier.send("WATCHDOG=1")
                    continue
                try:
                    result = await self.transport.poll(server, key, ts)
                    self.last_poll_at = time.time()
                    failed = int(result.get("failed", 0) or 0)
                    if failed == 1:
                        ts = str(result.get("ts") or ts)
                    elif failed in {2, 3}:
                        server, key, ts = await self.transport.get_long_poll_server()
                    elif failed:
                        raise RuntimeError("VK Long Poll reported an unknown failure")
                    else:
                        updates = result.get("updates", [])
                        if not isinstance(updates, list):
                            raise RuntimeError("VK Long Poll updates are malformed")
                        # Persist the whole accepted batch before advancing ts in
                        # memory. A crash after this point leaves durable work.
                        self.ingest_updates(updates)
                        ts = str(result.get("ts") or ts)
                    self.consecutive_poll_failures = 0
                    self.write_health("running")
                    self.notifier.send("WATCHDOG=1")
                except Exception:
                    self.consecutive_poll_failures += 1
                    self.write_health("degraded")
                    server = key = ts = ""
                    await asyncio.sleep(min(2 ** self.consecutive_poll_failures, 30))
                    server, key, ts = await self.transport.get_long_poll_server()
        self.lifecycle = "stopped"
        self.write_health()
        self.notifier.send("STOPPING=1")


def status_payload(settings: Settings, *, max_age_seconds: int) -> tuple[dict[str, Any], bool]:
    try:
        payload = json.loads(settings.health_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"lifecycle": "missing", "health_path": str(settings.health_path)}, False
    updated_at = float(payload.get("updated_at", 0) or 0)
    healthy = (
        payload.get("lifecycle") == "running"
        and updated_at >= time.time() - max_age_seconds
        and int(payload.get("consecutive_poll_failures", 0) or 0) < 5
        and int((payload.get("queue") or {}).get("dead", 0) or 0) == 0
    )
    payload["healthy"] = healthy
    payload["age_seconds"] = max(0.0, time.time() - updated_at)
    return payload, healthy


async def run_from_env() -> None:
    settings = Settings.from_env()
    dialog = VoidDialogEngine(DialogSettings.from_env())
    store = EventStore(settings.state_db_path)

    async def generate(
        user_id: int, text: str, event_kind: str, post_context: str
    ) -> str:
        platform = "vk_public" if event_kind != "private_message" else "vk"
        return await dialog.generate(
            user_id,
            text,
            platform=platform,
            source_context=post_context,
        )

    await CommunityBot(settings, store, generate).run()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VOID VK community private-message bot")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run VK Bots Long Poll")
    status_parser = subparsers.add_parser("status", help="read the local health file")
    status_parser.add_argument("--max-age-seconds", type=int, default=120)
    status_parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            asyncio.run(run_from_env())
            return 0
        settings = Settings.from_env(require_token=False)
        payload, healthy = status_payload(
            settings, max_age_seconds=max(1, args.max_age_seconds)
        )
        rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        print(rendered if args.json else rendered, flush=True)
        return 0 if healthy else 1
    except (ValueError, RuntimeError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
