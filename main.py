
from __future__ import annotations

import asyncio
import base64
import html
import json
import mimetypes
import os
import random
import re
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import feedparser
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

import character_state as void_character
import delegated_messaging
import duo_relationship
import editorial_policy
import gaming_vertical
from void_core import (
    CONTENT_PLAN,
    MATERIAL_VISUAL_PROMPT,
    MEANING_CARDS,
    MODE_SEMANTIC_THEMES,
    MODE_RUBRICS,
    NARRATIVE_SHAPES,
    RUBRIC_SCHEDULE,
    SCENE_AXES,
    SEMANTIC_THEMES,
    SEMANTIC_THEME_ORDER,
    TELEGRAM_VOID_SCHEDULE,
    VOID_CORE_PROMPT,
    VOID_VISUAL_CANON_PROMPT,
    platform_context,
)

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = os.getenv("CHANNEL_ID", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or "0")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "openai/gpt-5.4")
OPENAI_POST_MODEL = os.getenv("OPENAI_POST_MODEL", "openai/gpt-5.4-mini")
OPENAI_DIALOG_MODEL = os.getenv("OPENAI_DIALOG_MODEL", os.getenv("OPENAI_MODEL", "openai/gpt-5.4"))
DEFAULT_OPENAI_IMAGE_MODEL = "openai/gpt-image-2"
_configured_image_model = os.getenv("OPENAI_IMAGE_MODEL", "").strip()
if _configured_image_model and _configured_image_model != DEFAULT_OPENAI_IMAGE_MODEL:
    print(
        "configured OPENAI_IMAGE_MODEL does not match the required OpenRouter GPT Image 2 ID; "
        f"ignoring it and using {DEFAULT_OPENAI_IMAGE_MODEL}",
        flush=True,
    )
OPENAI_IMAGE_MODEL = DEFAULT_OPENAI_IMAGE_MODEL
OPENAI_IMAGE_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

# Voice messages use the official OpenAI API independently from OpenRouter.
VOICE_MESSAGES_ENABLED = os.getenv("VOICE_MESSAGES_ENABLED", "false").strip().lower() not in {"0", "false", "no", "off"}
VOICE_MESSAGES_ADMIN_ONLY = os.getenv("VOICE_MESSAGES_ADMIN_ONLY", "true").strip().lower() not in {"0", "false", "no", "off"}
OPENAI_VOICE_API_KEY = os.getenv("OPENAI_VOICE_API_KEY", "").strip()
OPENAI_VOICE_BASE_URL = os.getenv("OPENAI_VOICE_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe").strip()
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts").strip()
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "marin").strip()
OPENAI_REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2").strip()
VOICE_MAX_BYTES = max(1024 * 1024, min(int(os.getenv("VOICE_MAX_BYTES", str(15 * 1024 * 1024)) or 0), 20 * 1024 * 1024))
VOICE_MAX_DURATION_SECONDS = max(10, min(int(os.getenv("VOICE_MAX_DURATION_SECONDS", "300") or 0), 1200))

CROSSPOST_DAILY_LIMIT = int(os.getenv("CROSSPOST_DAILY_LIMIT", "2") or "2")
CROSSPOST_EXCHANGE_ENABLED = os.getenv("CROSSPOST_EXCHANGE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
CROSSPOST_EXCHANGE_AUTO_PUBLISH = os.getenv("CROSSPOST_EXCHANGE_AUTO_PUBLISH", "true").strip().lower() not in {"0", "false", "no", "off"}
CROSSPOST_EXCHANGE_DIR = Path(os.getenv("CROSSPOST_EXCHANGE_DIR", "/opt/bot_exchange").strip())
CROSSPOST_EXCHANGE_INTERVAL_SECONDS = max(60, int(os.getenv("CROSSPOST_EXCHANGE_INTERVAL_SECONDS", "300") or "300"))
CROSSPOST_EXCHANGE_MAX_PER_RUN = max(1, min(int(os.getenv("CROSSPOST_EXCHANGE_MAX_PER_RUN", "1") or "1"), 5))
VOID_TELEGRAM_AUTO_TIMES_RAW = os.getenv("VOID_TELEGRAM_AUTO_TIMES", "12:00,16:00,20:00,00:00").strip()

VK_USER_ACCESS_TOKEN = os.getenv("VK_USER_ACCESS_TOKEN", "")
VK_GROUP_ID = os.getenv("VK_GROUP_ID", "")
VK_API_VERSION = os.getenv("VK_API_VERSION", "5.199")
VK_DRY_RUN = os.getenv("VK_DRY_RUN", "true").strip().lower() not in {"0", "false", "no", "off"}
VK_MUSIC_TRACKS_FILE = os.getenv("VK_MUSIC_TRACKS_FILE", "data/vk_music_tracks.json")
VK_PHOTO_ACCESS_TOKEN = os.getenv("VK_PHOTO_ACCESS_TOKEN", "")

DB_PATH = "void.db"
try:
    MOSCOW_TZ = ZoneInfo("Europe/Moscow")
except ZoneInfoNotFoundError:
    MOSCOW_TZ = timezone(timedelta(hours=3), name="Europe/Moscow")


def parse_daily_times(value: str) -> tuple[str, ...]:
    """Return unique, normalized HH:MM schedule values in configured order."""
    result: list[str] = []
    for raw_time in value.split(","):
        raw_time = raw_time.strip()
        if not raw_time:
            continue
        try:
            hour_text, minute_text = raw_time.split(":", maxsplit=1)
            hour = int(hour_text)
            minute = int(minute_text)
            if hour not in range(24) or minute not in range(60):
                raise ValueError
        except ValueError:
            print(f"invalid VOID_TELEGRAM_AUTO_TIMES value skipped: {raw_time}", flush=True)
            continue
        normalized = f"{hour:02d}:{minute:02d}"
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


VOID_TELEGRAM_AUTO_TIMES = parse_daily_times(VOID_TELEGRAM_AUTO_TIMES_RAW)


def current_void_schedule_slot(
    now: datetime | None = None,
    schedule: tuple[str, ...] | None = None,
) -> str | None:
    """Return a date-qualified Moscow schedule slot for exact-minute deduplication."""
    current = now or datetime.now(MOSCOW_TZ)
    if current.tzinfo is not None:
        current = current.astimezone(MOSCOW_TZ)
    current_time = current.strftime("%H:%M")
    configured = VOID_TELEGRAM_AUTO_TIMES if schedule is None else schedule
    if current_time not in configured:
        return None
    return f"{current:%Y-%m-%d}:{current_time}"

router = Router()
auto_task: asyncio.Task | None = None
exchange_task: asyncio.Task | None = None
pending_delegation_purposes: dict[int, str] = {}
voice_openai_client: Any | None = None


@dataclass(frozen=True)
class TelegramPostPackage:
    text: str
    draft_id: int
    images: tuple[bytes, ...] = ()
    source_image_url: str | None = None
    no_image_reason: str | None = None


@dataclass(frozen=True)
class TelegramPublishOutcome:
    success: bool
    image_count: int = 0
    error: str | None = None


RSS_SOURCES = [
    {
        "name": "MIT Technology Review",
        "url": "https://www.technologyreview.com/feed/",
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
    },
    {
        "name": "Wired",
        "url": "https://www.wired.com/feed/rss",
    },
    {
        "name": "Ars Technica",
        "url": "https://feeds.arstechnica.com/arstechnica/index",
    },
]

VOID_TOPICS = {
    "AI": [
        "ai", "artificial intelligence", "openai", "llm", "chatgpt", "model",
        "agent", "agents", "automation", "neural", "dataset", "robot",
    ],
    "ATTENTION": [
        "attention", "feed", "scroll", "social media", "algorithm", "screen time",
        "recommendation", "platform", "instagram", "tiktok", "youtube",
    ],
    "CONTROL": [
        "privacy", "tracking", "surveillance", "data", "security", "ban",
        "regulation", "lawsuit", "policy", "moderation",
    ],
    "HUMAN": [
        "people", "human", "behavior", "work", "job", "health", "loneliness",
        "mental", "creator", "artist", "music", "culture",
    ],
    "FUTURE": [
        "future", "startup", "interface", "device", "wearable", "robot",
        "space", "battery", "chip", "compute", "research",
    ],
}

def build_rubric_header(mode: str, frequency: str = "HUMAN") -> str:
    base = MODE_RUBRICS.get(mode, "SIGNAL")
    if frequency and str(frequency).upper() not in {"HUMAN", "SIGNAL", "DIGEST"}:
        return f"{base} / {frequency.upper()}"
    return base


def inject_rubric_header(mode: str, frequency: str, post: str) -> str:
    header = build_rubric_header(mode, frequency)
    stripped = (post or "").strip()
    if not stripped:
        return header
    if stripped.startswith(header):
        return stripped
    return f"{header}\n\n{stripped}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
        check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        summary TEXT,
        url TEXT UNIQUE,
        source_name TEXT,
        frequency TEXT,
        score INTEGER DEFAULT 0,
        status TEXT DEFAULT 'NEW',
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT,
        title TEXT NOT NULL,
        post TEXT NOT NULL,
        source_name TEXT,
        source_url TEXT,
        frequency TEXT,
        publish_score INTEGER DEFAULT 5,
        created_at TEXT NOT NULL,
        published_at TEXT
    )
    """)
    draft_columns = {row[1] for row in cur.execute("PRAGMA table_info(drafts)").fetchall()}
    if "editorial_brief_json" not in draft_columns:
        cur.execute("ALTER TABLE drafts ADD COLUMN editorial_brief_json TEXT NOT NULL DEFAULT ''")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS catches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id INTEGER,
        user_id INTEGER,
        username TEXT,
        created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS published_urls (
        url TEXT PRIMARY KEY,
        draft_id INTEGER,
        published_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS vk_posts (
        draft_id INTEGER PRIMARY KEY,
        post_id INTEGER NOT NULL,
        owner_id INTEGER NOT NULL,
        attachments TEXT,
        music_track TEXT,
        published_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dialog_sessions (
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER DEFAULT 0,
    personality TEXT DEFAULT 'observer',
    last_activity TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS dialog_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS character_states (
        character_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL,
        core_version TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS content_signatures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        draft_id INTEGER,
        character_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT '',
        facet TEXT NOT NULL,
        intent TEXT NOT NULL,
        format TEXT NOT NULL,
        content_format TEXT NOT NULL DEFAULT 'text_story',
        content_kind TEXT NOT NULL DEFAULT 'text',
        semantic_theme TEXT NOT NULL DEFAULT '',
        meaning_key TEXT NOT NULL DEFAULT '',
        moral_axis TEXT NOT NULL DEFAULT '',
        scene_axis TEXT NOT NULL DEFAULT '',
        narrative_shape TEXT NOT NULL DEFAULT '',
        hook TEXT NOT NULL,
        media TEXT NOT NULL,
        topic TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    signature_columns = {row[1] for row in cur.execute("PRAGMA table_info(content_signatures)").fetchall()}
    if "content_format" not in signature_columns:
        cur.execute("ALTER TABLE content_signatures ADD COLUMN content_format TEXT NOT NULL DEFAULT 'text_story'")
    if "content_kind" not in signature_columns:
        cur.execute("ALTER TABLE content_signatures ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'text'")
    if "semantic_theme" not in signature_columns:
        cur.execute("ALTER TABLE content_signatures ADD COLUMN semantic_theme TEXT NOT NULL DEFAULT ''")
    if "draft_id" not in signature_columns:
        cur.execute("ALTER TABLE content_signatures ADD COLUMN draft_id INTEGER")
    for column in (
        "mode",
        "meaning_key",
        "moral_axis",
        "scene_axis",
        "narrative_shape",
    ):
        if column not in signature_columns:
            cur.execute(
                f"ALTER TABLE content_signatures "
                f"ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_void_signatures_created ON content_signatures(character_id, id)")

    cur.execute("""
    CREATE TABLE IF NOT EXISTS relationship_states (
        relationship_id TEXT PRIMARY KEY,
        state_json TEXT NOT NULL,
        version TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS private_thoughts (
        thought_id TEXT PRIMARY KEY,
        speaker TEXT NOT NULL,
        receiver TEXT NOT NULL,
        topic TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'new',
        created_at TEXT NOT NULL,
        consumed_at TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS saved_contacts (
        chat_id INTEGER PRIMARY KEY,
        owner_user_id INTEGER NOT NULL,
        alias TEXT,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending_name',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(owner_user_id, alias)
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS contact_naming_requests (
        prompt_message_id INTEGER PRIMARY KEY,
        owner_user_id INTEGER NOT NULL,
        contact_chat_id INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS reachable_peers (
        chat_id INTEGER PRIMARY KEY,
        display_name TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS delegation_invites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT NOT NULL UNIQUE,
        owner_user_id INTEGER NOT NULL,
        character_id TEXT NOT NULL,
        contact_label TEXT NOT NULL,
        purpose TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'waiting',
        contact_chat_id INTEGER,
        contact_name TEXT,
        max_turns INTEGER NOT NULL DEFAULT 20,
        turns_used INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS delegated_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        delegation_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS delegation_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id INTEGER NOT NULL,
        character_id TEXT NOT NULL,
        outcome TEXT NOT NULL,
        turns_used INTEGER NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_delegation_contact ON delegation_invites(contact_chat_id, status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_delegated_messages ON delegated_messages(delegation_id, id)")

    conn.commit()
    conn.close()


def create_delegation_invite(owner_user_id: int, contact_label: str, purpose: str, token: str, expires_at: str) -> int:
    init_db()
    conn = db()
    active = conn.execute(
        "SELECT id FROM delegation_invites WHERE owner_user_id=? AND status IN ('accepted','active','paused')",
        (owner_user_id,),
    ).fetchone()
    if active:
        conn.close()
        raise ValueError(f"Сначала заверши текущее поручение #{active['id']}.")
    stale = conn.execute("SELECT id FROM delegation_invites WHERE owner_user_id=?", (owner_user_id,)).fetchall()
    for row in stale:
        conn.execute("DELETE FROM delegated_messages WHERE delegation_id=?", (row["id"],))
    conn.execute("DELETE FROM delegation_invites WHERE owner_user_id=?", (owner_user_id,))
    now = now_iso()
    cur = conn.execute(
        """INSERT INTO delegation_invites(
            token, owner_user_id, character_id, contact_label, purpose, status,
            max_turns, expires_at, created_at, updated_at
        ) VALUES (?, ?, 'void', ?, ?, 'waiting', 20, ?, ?, ?)""",
        (token, owner_user_id, contact_label[:200], purpose[:1200], expires_at, now, now),
    )
    delegation_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return delegation_id


def remember_reachable_peer(chat_id: int, display_name: str, expires_at: str) -> None:
    init_db()
    conn = db()
    conn.execute("DELETE FROM reachable_peers WHERE expires_at<=?", (now_iso(),))
    conn.execute(
        """INSERT INTO reachable_peers(chat_id, display_name, expires_at, created_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET display_name=excluded.display_name, expires_at=excluded.expires_at""",
        (chat_id, display_name[:200], expires_at, now_iso()),
    )
    conn.commit()
    conn.close()


def register_contact_arrival(owner_user_id: int, chat_id: int, display_name: str) -> bool:
    init_db()
    conn = db()
    row = conn.execute("SELECT status FROM saved_contacts WHERE chat_id=?", (chat_id,)).fetchone()
    if row:
        conn.close()
        return False
    now = now_iso()
    conn.execute(
        "INSERT INTO saved_contacts(chat_id, owner_user_id, display_name, status, created_at, updated_at) VALUES (?, ?, ?, 'pending_name', ?, ?)",
        (chat_id, owner_user_id, display_name[:200], now, now),
    )
    conn.commit()
    conn.close()
    return True


def save_contact_naming_request(prompt_message_id: int, owner_user_id: int, contact_chat_id: int) -> None:
    conn = db()
    conn.execute(
        "INSERT OR REPLACE INTO contact_naming_requests(prompt_message_id, owner_user_id, contact_chat_id, created_at) VALUES (?, ?, ?, ?)",
        (prompt_message_id, owner_user_id, contact_chat_id, now_iso()),
    )
    conn.commit()
    conn.close()


def name_contact_from_reply(prompt_message_id: int, owner_user_id: int, alias: str) -> dict | None:
    clean_alias = " ".join((alias or "").split()).strip()[:80]
    if not clean_alias:
        return None
    conn = db()
    request = conn.execute(
        "SELECT * FROM contact_naming_requests WHERE prompt_message_id=? AND owner_user_id=?",
        (prompt_message_id, owner_user_id),
    ).fetchone()
    if not request:
        conn.close()
        return None
    duplicate = conn.execute(
        "SELECT chat_id FROM saved_contacts WHERE owner_user_id=? AND lower(alias)=lower(?) AND chat_id<>?",
        (owner_user_id, clean_alias, request["contact_chat_id"]),
    ).fetchone()
    if duplicate:
        conn.close()
        raise ValueError("Такое имя уже занято другим контактом.")
    conn.execute(
        "UPDATE saved_contacts SET alias=?, status='saved', updated_at=? WHERE chat_id=?",
        (clean_alias, now_iso(), request["contact_chat_id"]),
    )
    conn.execute("DELETE FROM contact_naming_requests WHERE prompt_message_id=?", (prompt_message_id,))
    row = conn.execute("SELECT * FROM saved_contacts WHERE chat_id=?", (request["contact_chat_id"],)).fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def list_saved_contacts(owner_user_id: int) -> list[dict]:
    init_db()
    conn = db()
    rows = conn.execute(
        "SELECT chat_id, alias, display_name FROM saved_contacts WHERE owner_user_id=? AND status='saved' ORDER BY alias",
        (owner_user_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_named_contact(owner_user_id: int, chat_id: int, display_name: str, alias: str) -> dict:
    clean_alias = " ".join((alias or "").split()).strip()[:80]
    if not clean_alias:
        raise ValueError("Имя контакта пустое.")
    init_db()
    conn = db()
    duplicate = conn.execute(
        "SELECT chat_id FROM saved_contacts WHERE owner_user_id=? AND lower(alias)=lower(?) AND chat_id<>?",
        (owner_user_id, clean_alias, chat_id),
    ).fetchone()
    if duplicate:
        conn.close()
        raise ValueError("Такое имя уже занято другим контактом.")
    now = now_iso()
    conn.execute(
        """INSERT INTO saved_contacts(chat_id, owner_user_id, alias, display_name, status, created_at, updated_at)
           VALUES (?, ?, ?, ?, 'saved', ?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET owner_user_id=excluded.owner_user_id,
               alias=excluded.alias, display_name=excluded.display_name, status='saved', updated_at=excluded.updated_at""",
        (chat_id, owner_user_id, clean_alias, display_name[:200], now, now),
    )
    row = conn.execute("SELECT * FROM saved_contacts WHERE chat_id=?", (chat_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row)


def list_previous_contact_ids(owner_user_id: int, limit: int = 30) -> list[int]:
    init_db()
    conn = db()
    rows = conn.execute(
        """SELECT DISTINCT user_id FROM dialog_messages
           WHERE user_id<>? AND user_id NOT IN (
               SELECT chat_id FROM saved_contacts WHERE owner_user_id=? AND status='saved'
           ) ORDER BY user_id DESC LIMIT ?""",
        (owner_user_id, owner_user_id, max(1, limit)),
    ).fetchall()
    conn.close()
    return [int(row["user_id"]) for row in rows]


def get_reachable_peer(chat_id: int) -> dict | None:
    init_db()
    conn = db()
    conn.execute("DELETE FROM reachable_peers WHERE expires_at<=?", (now_iso(),))
    row = conn.execute("SELECT * FROM reachable_peers WHERE chat_id=?", (chat_id,)).fetchone()
    conn.commit()
    conn.close()
    return dict(row) if row else None


def get_delegation(delegation_id: int) -> dict | None:
    init_db()
    conn = db()
    row = conn.execute("SELECT * FROM delegation_invites WHERE id=?", (delegation_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def accept_delegation_invite(token: str, contact_chat_id: int, contact_name: str) -> dict | None:
    init_db()
    conn = db()
    now = now_iso()
    row = conn.execute(
        "SELECT * FROM delegation_invites WHERE token=? AND status='waiting' AND expires_at>?",
        (token, now),
    ).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute(
        "UPDATE delegation_invites SET status='accepted', contact_chat_id=?, contact_name=?, updated_at=? WHERE id=?",
        (contact_chat_id, contact_name[:200], now, row["id"]),
    )
    conn.commit()
    conn.close()
    result = dict(row)
    result.update(status="accepted", contact_chat_id=contact_chat_id, contact_name=contact_name[:200], updated_at=now)
    return result


def set_delegation_status(delegation_id: int, status: str) -> None:
    conn = db()
    conn.execute("UPDATE delegation_invites SET status=?, updated_at=? WHERE id=?", (status, now_iso(), delegation_id))
    conn.commit()
    conn.close()


def get_active_delegation(contact_chat_id: int) -> dict | None:
    init_db()
    conn = db()
    row = conn.execute(
        """SELECT * FROM delegation_invites WHERE contact_chat_id=? AND status='active' AND expires_at>?
           ORDER BY id DESC LIMIT 1""",
        (contact_chat_id, now_iso()),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def save_delegated_message(delegation_id: int, role: str, content: str) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO delegated_messages(delegation_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (delegation_id, role, content[:8000], now_iso()),
    )
    conn.commit()
    conn.close()


def get_delegated_history(delegation_id: int, limit: int = 10) -> list[dict[str, str]]:
    conn = db()
    rows = conn.execute(
        "SELECT role, content FROM delegated_messages WHERE delegation_id=? ORDER BY id DESC LIMIT ?",
        (delegation_id, max(1, limit)),
    ).fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def increment_delegation_turns(delegation_id: int) -> int:
    conn = db()
    conn.execute("UPDATE delegation_invites SET turns_used=turns_used+1, updated_at=? WHERE id=?", (now_iso(), delegation_id))
    row = conn.execute("SELECT turns_used FROM delegation_invites WHERE id=?", (delegation_id,)).fetchone()
    conn.commit()
    conn.close()
    return int(row["turns_used"]) if row else 0


def purge_delegation(delegation_id: int, outcome: str) -> None:
    conn = db()
    row = conn.execute("SELECT * FROM delegation_invites WHERE id=?", (delegation_id,)).fetchone()
    if row:
        conn.execute(
            "INSERT INTO delegation_audit(owner_user_id, character_id, outcome, turns_used, created_at) VALUES (?, ?, ?, ?, ?)",
            (row["owner_user_id"], row["character_id"], outcome[:80], row["turns_used"], now_iso()),
        )
        conn.execute("DELETE FROM delegated_messages WHERE delegation_id=?", (delegation_id,))
        if row["contact_chat_id"] is not None:
            conn.execute("DELETE FROM reachable_peers WHERE chat_id=?", (row["contact_chat_id"],))
        conn.execute("DELETE FROM delegation_invites WHERE id=?", (delegation_id,))
    conn.commit()
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def load_character_state() -> void_character.CharacterState:
    conn = db()
    row = conn.execute(
        "SELECT state_json FROM character_states WHERE character_id=?",
        (void_character.CHARACTER_ID,),
    ).fetchone()
    conn.close()
    if not row:
        state = void_character.CharacterState()
        save_character_state(state)
        return state
    try:
        raw = json.loads(row["state_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    return void_character.normalize_state(raw if isinstance(raw, dict) else {})


def save_character_state(state: void_character.CharacterState) -> None:
    normalized = void_character.normalize_state(state.to_dict())
    conn = db()
    conn.execute(
        """
        INSERT INTO character_states(character_id, state_json, core_version, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(character_id) DO UPDATE SET
            state_json=excluded.state_json,
            core_version=excluded.core_version,
            updated_at=excluded.updated_at
        """,
        (
            void_character.CHARACTER_ID,
            json.dumps(normalized.to_dict(), ensure_ascii=False),
            normalized.core_version,
            now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def apply_character_event(event: str) -> void_character.CharacterState:
    state = void_character.apply_event(load_character_state(), event)
    save_character_state(state)
    return state


def set_character_axis(axis: str, value: int) -> void_character.CharacterState:
    state = void_character.set_axis(load_character_state(), axis, value)
    save_character_state(state)
    return state


def get_recent_content_signatures(limit: int = 16) -> list[dict[str, str]]:
    conn = db()
    rows = conn.execute(
        """
        SELECT signatures.platform, signatures.mode, signatures.facet, signatures.intent,
               signatures.format, signatures.content_format,
               signatures.content_kind, signatures.semantic_theme,
               signatures.meaning_key, signatures.moral_axis,
               signatures.scene_axis, signatures.narrative_shape,
               signatures.hook, signatures.media, signatures.topic,
               signatures.created_at
        FROM content_signatures AS signatures
        JOIN drafts ON drafts.id = signatures.draft_id
        WHERE signatures.character_id=?
          AND drafts.published_at IS NOT NULL
        ORDER BY signatures.id DESC
        LIMIT ?
        """,
        (void_character.CHARACTER_ID, max(1, limit)),
    ).fetchall()
    conn.close()
    return [dict(row) for row in reversed(rows)]


def record_content_signature(
    plan: dict[str, str],
    topic: str,
    draft_id: int,
) -> None:
    conn = db()
    conn.execute(
        """
        INSERT INTO content_signatures(
            draft_id, character_id, platform, mode, facet, intent, format,
            content_format, content_kind, semantic_theme, meaning_key,
            moral_axis, scene_axis, narrative_shape, hook, media, topic, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(draft_id),
            void_character.CHARACTER_ID,
            str(plan.get("platform", "telegram")),
            str(plan.get("mode", "")),
            str(plan.get("facet", "observer")),
            str(plan.get("intent", "наблюдать")),
            str(plan.get("format", "тихое наблюдение")),
            str(plan.get("content_format", "text_story")),
            str(plan.get("content_kind", "text")),
            str(plan.get("semantic_theme", "")),
            str(plan.get("meaning_key", "")),
            str(plan.get("moral_axis", "")),
            str(plan.get("scene_axis", "")),
            str(plan.get("narrative_shape", "")),
            str(plan.get("hook", "деталь")),
            str(plan.get("media", "кинематографический кадр")),
            topic[:1000],
            now_iso(),
        ),
    )
    conn.execute(
        """
        DELETE FROM content_signatures
        WHERE character_id=? AND id NOT IN (
            SELECT id FROM content_signatures
            WHERE character_id=?
            ORDER BY id DESC
            LIMIT 80
        )
        """,
        (void_character.CHARACTER_ID, void_character.CHARACTER_ID),
    )
    conn.commit()
    conn.close()


def character_event_for_mode(mode: str) -> str:
    if mode in {"frequency", "culture", "midnight"}:
        return "beauty"
    if mode in {"news", "digest", "archive"}:
        return "noise"
    if mode == "future":
        return "naz_challenge"
    return "quiet"


def build_character_directive(
    topic: str,
    platform: str,
    mode: str,
    persist_event: bool = True,
    semantic_theme: str = "",
) -> tuple[void_character.CharacterState, dict[str, str], str]:
    event = character_event_for_mode(mode)
    if persist_event:
        state = apply_character_event(event)
    else:
        state = void_character.apply_event(load_character_state(), event)
    recent_signatures = get_recent_content_signatures()
    plan = void_character.plan_content(
        state,
        recent_signatures,
        topic=topic,
        platform=platform,
    )
    plan["mode"] = mode
    if semantic_theme:
        if semantic_theme not in SEMANTIC_THEMES:
            raise ValueError("unknown semantic theme")
        plan["semantic_theme"] = semantic_theme
        plan["semantic_theme_instruction"] = SEMANTIC_THEMES[semantic_theme]
        plan.update(
            select_editorial_axes(
                semantic_theme,
                recent_signatures,
            )
        )
    return state, plan, void_character.prompt_context(state, plan)


def load_relationship_state() -> duo_relationship.RelationshipState:
    init_db()
    conn = db()
    row = conn.execute(
        "SELECT state_json FROM relationship_states WHERE relationship_id='naz-void'"
    ).fetchone()
    conn.close()
    if not row:
        state = duo_relationship.RelationshipState()
        save_relationship_state(state)
        return state
    try:
        raw = json.loads(row["state_json"] or "{}")
    except json.JSONDecodeError:
        raw = {}
    return duo_relationship.normalize_state(raw if isinstance(raw, dict) else {})


def save_relationship_state(state: duo_relationship.RelationshipState) -> None:
    normalized = duo_relationship.normalize_state(state.to_dict())
    conn = db()
    conn.execute(
        """
        INSERT INTO relationship_states(relationship_id, state_json, version, updated_at)
        VALUES ('naz-void', ?, ?, ?)
        ON CONFLICT(relationship_id) DO UPDATE SET
            state_json=excluded.state_json, version=excluded.version, updated_at=excluded.updated_at
        """,
        (json.dumps(normalized.to_dict(), ensure_ascii=False), normalized.version, now_iso()),
    )
    conn.commit()
    conn.close()


def apply_relationship_event(event: str, *, topic: str = "", note: str = "") -> duo_relationship.RelationshipState:
    state = duo_relationship.apply_event(load_relationship_state(), event, topic=topic, note=note)
    save_relationship_state(state)
    return state


def save_private_thought(payload: dict[str, Any], status: str = "new") -> None:
    ok, reason = duo_relationship.validate_private_thought_payload(payload)
    if not ok:
        raise ValueError(reason)
    conn = db()
    conn.execute(
        """
        INSERT OR IGNORE INTO private_thoughts(
            thought_id, speaker, receiver, topic, payload_json, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["thought_id"], payload["speaker"], payload["receiver"],
            str(payload.get("topic", ""))[:1000], json.dumps(payload, ensure_ascii=False), status, now_iso(),
        ),
    )
    conn.commit()
    conn.close()


def moscow_day() -> str:
    return datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()


def crosspost_counter_key(direction: str) -> str:
    return f"crosspost:{direction}:{moscow_day()}"


def crosspost_count(direction: str) -> int:
    return int(get_setting(crosspost_counter_key(direction), "0") or "0")


def can_crosspost(direction: str) -> bool:
    return crosspost_count(direction) < CROSSPOST_DAILY_LIMIT


def mark_crosspost(direction: str) -> None:
    key = crosspost_counter_key(direction)
    set_setting(key, str(int(get_setting(key, "0") or "0") + 1))


def crosspost_status_text() -> str:
    return (
        "Cross-post today:\n"
        f"VOID -> Naz AI Bot: {crosspost_count('void_to_naz')}/{CROSSPOST_DAILY_LIMIT}\n"
        f"Naz AI Bot -> VOID: {crosspost_count('naz_to_void')}/{CROSSPOST_DAILY_LIMIT}\n"
        f"Exchange: {'enabled' if CROSSPOST_EXCHANGE_ENABLED else 'disabled'}"
    )


def get_dialog_session(user_id: int) -> dict:
    conn = db()
    row = conn.execute(
        """
        SELECT enabled, personality, last_activity
        FROM dialog_sessions
        WHERE user_id=?
        """,
        (user_id,),
    ).fetchone()

    if not row:
        conn.execute(
            """
            INSERT INTO dialog_sessions(user_id)
            VALUES(?)
            """,
            (user_id,),
        )
        conn.commit()

        row = conn.execute(
            """
            SELECT enabled, personality, last_activity
            FROM dialog_sessions
            WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()

    conn.close()

    return {
        "enabled": row["enabled"],
        "personality": row["personality"],
        "last_activity": row["last_activity"],
    }


def set_dialog_enabled(user_id: int, enabled: bool) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO dialog_sessions(user_id, enabled, last_activity)
        VALUES(?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            enabled=excluded.enabled,
            last_activity=excluded.last_activity
        """,
        (
            user_id,
            1 if enabled else 0,
            now_iso(),
        ),
    )

    conn.commit()
    conn.close()


def set_personality(user_id: int, personality: str) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO dialog_sessions(user_id, personality)
        VALUES(?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET personality=excluded.personality
        """,
        (
            user_id,
            personality,
        ),
    )

    conn.commit()
    conn.close()


def save_dialog_message(user_id: int, role: str, content: str) -> None:
    conn = db()

    conn.execute(
        """
        INSERT INTO dialog_messages(
            user_id,
            role,
            content,
            created_at
        )
        VALUES(?, ?, ?, ?)
        """,
        (
            user_id,
            role,
            content,
            now_iso(),
        ),
    )

    conn.execute(
        """
        UPDATE dialog_sessions
        SET last_activity=?
        WHERE user_id=?
        """,
        (
            now_iso(),
            user_id,
        ),
    )

    conn.commit()
    conn.close()


def get_dialog_context(user_id: int, limit: int = 10) -> list[dict]:
    conn = db()

    rows = conn.execute(
        """
        SELECT role, content
        FROM dialog_messages
        WHERE user_id=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (
            user_id,
            limit,
        ),
    ).fetchall()

    conn.close()

    rows = list(reversed(rows))

    return [
        {
            "role": row["role"],
            "content": row["content"],
        }
        for row in rows
    ]


def clear_dialog_context(user_id: int) -> None:
    conn = db()

    conn.execute(
        """
        DELETE FROM dialog_messages
        WHERE user_id=?
        """,
        (user_id,),
    )

    conn.commit()
    conn.close()


def build_memory_note(user_text: str, assistant_reply: str) -> str | None:
    text = (user_text or "").strip()
    if not text:
        return None

    cleaned = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    words = [w for w in cleaned.split() if len(w) > 2]
    stopwords = {
        "это", "что", "как", "почему", "когда", "где", "мне", "меня", "я", "ты", "ты", "мы",
        "вам", "тебе", "у", "в", "на", "из", "за", "по", "для", "но", "и", "или", "а", "то",
        "если", "так", "можно", "хочу", "нужно", "сейчас", "про", "с", "со", "не", "ну",
    }
    topic_words = [w.lower() for w in words if w.lower() not in stopwords]
    if not topic_words:
        return None

    topic = " ".join(topic_words[:6]).strip()
    if not topic:
        return None

    return f"Пользователь обсуждает: {topic}"


def get_dialog_memory(user_id: int) -> str | None:
    conn = db()
    row = conn.execute(
        """
        SELECT content
        FROM dialog_messages
        WHERE user_id=? AND role='memory'
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    conn.close()

    return row["content"] if row else None


def get_personality_style(personality: str) -> str:
    styles = {
        "observer": "Наблюдатель: коротко, спокойно, с сухой иронией, без лишних эмоций.",
        "analyst": "Аналитик: структурно, по фактам, с кратким выводом и ясной логикой.",
        "philosopher": "Философ: глубже, с метафорой, но без занудства.",
        "cynic": "Циник: язвительно, остро, но умно, без тупой агрессии.",
        "calm": "Спокойный: мягко, ровно, без давления и лишней драматизации.",
    }
    return styles.get(personality, styles["observer"])


def build_dialog_prompt(user_text: str, personality: str, history: list[dict], memory_note: str | None = None) -> str:
    history_block = ""
    if history:
        turns = []
        for turn in history:
            role = "Пользователь" if turn.get("role") == "user" else "VOID"
            turns.append(f"{role}: {turn.get('content', '').strip()}")
        history_block = "\n".join(turns)
        history_block = f"\n\nКонтекст предыдущих сообщений:\n{history_block}\n"

    memory_block = ""
    if memory_note:
        memory_block = f"\n\nКраткая память:\n{memory_note}\n"

    personality_style = get_personality_style(personality)
    character_context = void_character.dialogue_context(load_character_state())

    return f"""
{VOID_CORE_PROMPT}

{platform_context("telegram")}

    Ты VOID Entity.
    Ты — наблюдательный, сухой, чуть ироничный собеседник.
    Отвечай кратко, по-русски, без markdown.
    {personality_style}
    {character_context}
    {memory_block}{history_block}
    Текущее сообщение пользователя: {user_text}
    """.strip()


BTN_GUIDE = "🧭 Как общаться"
BTN_PERSONA = "🎭 Характер"
BTN_TOPICS = "🛰 Темы"
BTN_STATUS = "📊 Статус"
BTN_RESET = "🧹 Очистить контекст"
BTN_BACK = "⬅️ Назад"


def reply_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_GUIDE), KeyboardButton(text=BTN_PERSONA)],
            [KeyboardButton(text=BTN_TOPICS), KeyboardButton(text=BTN_STATUS)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def reply_guide_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_RESET)],
            [KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def reply_topics_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📰 Новости"), KeyboardButton(text="🎵 Музыка")],
            [KeyboardButton(text="🔮 Будущее"), KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=BTN_GUIDE, callback_data="void:guide"),
            InlineKeyboardButton(text=BTN_PERSONA, callback_data="void:persona"),
        ],
        [
            InlineKeyboardButton(text=BTN_TOPICS, callback_data="void:quick"),
            InlineKeyboardButton(text=BTN_STATUS, callback_data="void:status"),
        ],
    ])


def guide_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text=BTN_RESET, callback_data="void:reset"),
        ],
        [
            InlineKeyboardButton(text=BTN_BACK, callback_data="void:menu"),
        ],
    ])


def persona_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Наблюдатель", callback_data="void:persona:observer")],
        [InlineKeyboardButton(text="📊 Аналитик", callback_data="void:persona:analyst")],
        [InlineKeyboardButton(text="🎭 Философ", callback_data="void:persona:philosopher")],
        [InlineKeyboardButton(text="😏 Циник", callback_data="void:persona:cynic")],
        [InlineKeyboardButton(text="😌 Спокойный", callback_data="void:persona:calm")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="void:menu")],
    ])


def quick_actions_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📰 Новости", callback_data="void:quick:news")],
        [InlineKeyboardButton(text="🎵 Музыка", callback_data="void:quick:music")],
        [InlineKeyboardButton(text="🔮 Будущее", callback_data="void:quick:future")],
        [InlineKeyboardButton(text="🧭 Как общаться", callback_data="void:guide")],
        [InlineKeyboardButton(text="🎭 Характер", callback_data="void:persona")],
        [InlineKeyboardButton(text=BTN_BACK, callback_data="void:menu")],
    ])


def welcome_text() -> str:
    return (
        "VOID online.\n\n"
        "Я не командная строка. Просто напиши мысль, вопрос, ссылку, новость или странное наблюдение.\n\n"
        "Я отвечу коротко, по делу и чуть сбоку: где тут сигнал, что в этом человеческого и почему это вообще цепляет.\n\n"
        "Кнопки ниже — это навигация, если хочется настроить тон или понять, с чего начать."
    )


def delegation_from_row(row: dict[str, Any]) -> delegated_messaging.Delegation:
    return delegated_messaging.Delegation(
        character_id=str(row["character_id"]), owner_user_id=int(row["owner_user_id"]),
        contact_chat_id=int(row["contact_chat_id"]),
        contact_name=str(row.get("contact_name") or row.get("contact_label") or "Собеседник"),
        purpose=str(row["purpose"]), status=str(row["status"]), max_turns=int(row["max_turns"]),
        turns_used=int(row["turns_used"]), expires_at=str(row["expires_at"]),
    )


async def ensure_contact_named(message: Message) -> None:
    if not message.from_user or not ADMIN_ID or message.from_user.id == ADMIN_ID:
        return
    if not register_contact_arrival(ADMIN_ID, message.from_user.id, message.from_user.full_name):
        return
    prompt = await message.bot.send_message(
        ADMIN_ID,
        f"Мне впервые написал {message.from_user.full_name} (Telegram ID {message.from_user.id}).\n"
        "Как записать контакт? Ответь на это сообщение одним именем, например: Диман",
    )
    save_contact_naming_request(prompt.message_id, ADMIN_ID, message.from_user.id)


async def start_saved_contact_delegation(message: Message, contact: dict[str, Any], purpose: str) -> None:
    token = delegated_messaging.invite_token()
    expires = (delegated_messaging.utc_now() + timedelta(hours=24)).isoformat(timespec="seconds")
    delegation_id = create_delegation_invite(message.from_user.id, str(contact["alias"]), purpose, token, expires)
    row = accept_delegation_invite(token, int(contact["chat_id"]), str(contact["alias"]))
    if not row:
        raise RuntimeError("Не удалось привязать сохранённый контакт.")
    delegation = delegation_from_row(row)
    intro = delegated_messaging.introduction(delegation)
    await message.bot.send_message(delegation.contact_chat_id, intro)
    save_delegated_message(delegation_id, "assistant", intro)
    set_delegation_status(delegation_id, "active")
    await message.answer(
        f"Нашёл {contact['alias']} и начал поручение #{delegation_id}. После разговора удалю сессию и переписку."
    )


async def handle_delegated_reply(message: Message, text: str) -> bool:
    if not message.from_user:
        return False
    row = get_active_delegation(message.from_user.id)
    if not row:
        return False
    delegation_id = int(row["id"])
    delegation = delegation_from_row(row)
    if delegated_messaging.is_stop(text):
        await message.answer("Остановился. Переписка этой сессии удалена.")
        await message.bot.send_message(delegation.owner_user_id, f"Собеседник остановил поручение #{delegation_id}.")
        purge_delegation(delegation_id, "contact_stopped")
        return True
    risks = delegated_messaging.assess_risk(text)
    save_delegated_message(delegation_id, "contact", text)
    if risks:
        set_delegation_status(delegation_id, "paused")
        await message.answer("Тут нужно подтверждение Назара. Я поставил разговор на паузу.")
        await message.bot.send_message(
            delegation.owner_user_id,
            f"Поручение #{delegation_id} на паузе ({', '.join(risks)}). Ответить: /delegate_reply {delegation_id} текст",
        )
        return True
    history = get_delegated_history(delegation_id, 24)
    prompt = delegated_messaging.system_prompt(
        delegation=delegation,
        character_context=void_character.dialogue_context(load_character_state()),
        history=history,
    )
    reply = await asyncio.to_thread(call_ai, prompt, text, 500, OPENAI_DIALOG_MODEL)
    if reply == "OWNER_CONFIRMATION_REQUIRED" or delegated_messaging.assess_risk(reply):
        set_delegation_status(delegation_id, "paused")
        await message.answer("Мне нужно свериться с Назаром. Поставил разговор на паузу.")
        await message.bot.send_message(
            delegation.owner_user_id,
            f"VOID остановил поручение #{delegation_id}. Ответить: /delegate_reply {delegation_id} текст",
        )
        return True
    await message.answer(reply)
    save_delegated_message(delegation_id, "assistant", reply)
    turns = increment_delegation_turns(delegation_id)
    if turns >= delegation.max_turns:
        await message.answer("На этом поручение завершено. Спасибо за разговор.")
        await message.bot.send_message(delegation.owner_user_id, f"Поручение #{delegation_id} завершено по лимиту.")
        purge_delegation(delegation_id, "turn_limit")
    return True


def commands_text() -> str:
    return (
        "VOID commands\n\n"
        "Core:\n"
        "/start - open VOID\n"
        "/help - command rooms\n"
        "/commands - this list\n"
        "/vk_commands - VK publisher and music commands\n\n"
        "Character:\n"
        "/character - current VOID facet and mood\n"
        "/character_event event - apply an event (admin)\n\n"
        "/character_set axis 0-100 - adjust state (admin)\n\n"
        "/character_simulate 10 - preview future states without saving\n"
        "/relationship - Naz/VOID relationship state\n"
        "/relationship_event event topic - apply relationship event (admin)\n\n"
        "Drafts:\n"
        "/scan - find fresh signals\n"
        "/discuss_news - start one shared Naz/VOID news conversation\n"
        "/candidates - show candidates\n"
        "/draft ID - create draft from candidate\n"
        "/drafts - show drafts\n"
        "/preview ID - show full draft\n"
        "/publish ID - publish draft to Telegram\n\n"
        "Gaming:\n"
        "/gaming topic - create a VOID gaming draft\n"
        "/gaming_commercial topic - gaming draft with a soft product test\n"
        "/gaming_plan topic - preview rubric and format\n\n"
        "Private conversation:\n"
        "/void text - prepare a private-dialogue fragment for Naz\n"
        "/publish_void text - queue a VOID fragment for Naz adaptation\n"
        "/thought_to_naz text - send an unpublished private thought to Naz\n"
        "/thought_from_naz text - digest an unpublished Naz thought\n"
        "Напиши Диману, чтобы… - начать разговор с сохранённым контактом\n"
        "/contact_candidates - прежние незаписанные собеседники\n"
        "/contact_add ID Имя - сохранить прежнего собеседника\n"
        "/delegate_stop ID - завершить поручение\n"
        "/cross_status - today's cross-post counters\n"
        "/cross_to_naz ID - extract a fragment and queue it for Naz\n"
        "/cross_from_naz text - adapt Naz AI Bot post to VOID\n\n"
        "Rubric schedule:\n"
        "/rubric_schedule - show rubric windows\n"
        "/vk_schedule_draft - create one scheduled VK draft now\n\n"
        "Telegram schedules:\n"
        "/telegram_schedule - show VOID Telegram windows\n"
        "/void_schedule_now - publish one scheduled VOID Telegram post\n"
        "Stats:\n"
        "/stats - database stats"
    )


def vk_commands_text() -> str:
    return (
        "VK commands\n\n"
        "Publisher:\n"
        "/vk_status - VK env and mode status\n"
        "/vk_test text - dry-run raw VK text post\n"
        "/vk_test --yes text - publish raw VK test post\n"
        "/publish_vk ID - dry-run draft VK post\n"
        "/publish_vk --yes ID - publish draft to VK\n\n"
        "Rubric schedule:\n"
        "/rubric_schedule - show current rubric windows\n"
        "/vk_schedule_draft - make one scheduled VK draft now\n\n"
        "Music cache:\n"
        "/vk_music_status - show loaded track count\n"
        "/vk_music_import Artist - Track | link | tags - import tracks from text\n"
        "/vk_music_sync URL - planned browser sync from a VK playlist\n"
        "/vk_music_sync URL night,electronic,melancholy - planned sync with base tags\n\n"
        "Notes:\n"
        "- /publish_vk blocks duplicate VK posts by draft id.\n"
        "- VOID project owns VOID VK drafts only; Naz VK automation belongs in the Naz project.\n"
        "- API publishing can still fall back to soundtrack text if browser publishing is not used."
    )


def guide_text() -> str:
    return (
        "🧭 Как со мной общаться\n\n"
        "Можно писать обычным текстом, без команд.\n\n"
        "Примеры:\n"
        "• почему люди устают от AI-новостей\n"
        "• вот ссылка, найди в ней сигнал\n"
        "• сделай мысль жёстче и короче\n"
        "• что в этой новости говорит о будущем\n\n"
        "Я помню ближайший контекст диалога. Если разговор уехал, очисти контекст кнопкой ниже."
    )

@router.callback_query(F.data == "void:menu")
async def void_menu_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        welcome_text(),
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:guide")
@router.callback_query(F.data == "void:dialog")
async def void_guide_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        guide_text(),
        reply_markup=guide_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:dialog:on")
async def void_dialog_on_callback(callback: CallbackQuery):
    set_dialog_enabled(callback.from_user.id, True)

    await callback.message.edit_text(
        "Диалог теперь всегда включён. Просто пиши текст.",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:dialog:off")
async def void_dialog_off_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "Я больше не выключаю диалог. Если нужно начать заново, очисти контекст.",
        reply_markup=guide_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:reset")
async def void_reset_callback(callback: CallbackQuery):
    clear_dialog_context(callback.from_user.id)
    await callback.message.edit_text(
        "🧹 Контекст очищен. Можно начинать с новой мысли.",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:persona")
async def void_persona_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎭 Выбери характер VOID:",
        reply_markup=persona_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("void:persona:"))
async def void_persona_set_callback(callback: CallbackQuery):
    personality = callback.data.split(":")[-1]
    set_personality(callback.from_user.id, personality)

    await callback.message.edit_text(
        f"🎭 Характер VOID изменён: {personality}",
        reply_markup=main_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:status")
async def void_status_callback(callback: CallbackQuery):
    session = get_dialog_session(callback.from_user.id)
    text = (
        f"📊 Статус VOID\n\n"
        f"Диалог: всегда ON\n"
        f"Характер: {session['personality']}"
    )
    if callback.from_user and callback.from_user.id == ADMIN_ID:
        text += f"\n\nКросспостинг:\n{crosspost_status_text()}"

    await callback.message.edit_text(
        text,
        reply_markup=main_keyboard(),
    )
    await callback.answer()

def is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id == ADMIN_ID)


def admin_required() -> str:
    return "VOID пока слушает только администратора. Публичный режим будет позже."


def score_item(title: str, summary: str = "") -> tuple[str, int]:
    text = f"{title} {summary}".lower()
    scores: dict[str, int] = {}

    for topic, keywords in VOID_TOPICS.items():
        scores[topic] = sum(2 if " " in kw and kw in text else 1 for kw in keywords if kw in text)

    best = max(scores, key=scores.get)
    score = scores[best]

    if score == 0:
        return "NOISE", 0

    if "ai" in text or "artificial intelligence" in text or "openai" in text:
        score += 3
        best = "AI"

    return best, min(score, 20)


def pick_post_mode_and_frequency(title: str, summary: str = "") -> tuple[str, str]:
    text = f"{title} {summary}".lower()
    frequency = "HUMAN"

    if any(token in text for token in ["ai", "artificial intelligence", "openai", "llm", "model", "agent", "automation"]):
        frequency = "AI"
    elif any(token in text for token in ["privacy", "security", "tracking", "regulation", "policy", "surveillance", "data"]):
        frequency = "CONTROL"
    elif any(token in text for token in ["attention", "social media", "algorithm", "scroll", "feed", "platform", "instagram", "tiktok", "youtube"]):
        frequency = "ATTENTION"
    elif any(token in text for token in ["future", "startup", "interface", "device", "wearable", "space", "chip", "battery", "research"]):
        frequency = "FUTURE"
    elif any(token in text for token in ["people", "human", "behavior", "work", "job", "health", "loneliness", "mental", "culture", "music", "song", "album", "artist", "festival", "streaming"]):
        frequency = "HUMAN"

    if any(token in text for token in ["night", "midnight", "sleep", "dream", "loneliness", "isolation", "dark"]):
        return "midnight", frequency

    if any(token in text for token in ["music", "song", "album", "artist", "festival", "streaming", "playlist", "dj", "sound"]):
        if any(token in text for token in ["culture", "behavior", "attention", "platform", "algorithm", "social media", "feed", "scroll"]):
            return "observation", frequency
        if any(token in text for token in ["night", "midnight", "dream", "loneliness", "late", "after hours"]):
            return "midnight", frequency
        return "frequency", frequency

    if any(token in text for token in ["privacy", "security", "tracking", "regulation", "policy", "surveillance", "data"]):
        return "news", frequency

    if any(token in text for token in ["behavior", "habit", "attention", "platform", "scroll", "feed", "social media", "culture", "people"]):
        return "observation", frequency

    if any(token in text for token in ["future", "research", "device", "chip", "battery", "interface", "space", "wearable"]):
        return "future", frequency

    if any(token in text for token in ["tech", "ai", "model", "startup", "policy", "regulation", "security", "privacy"]):
        return "news", frequency

    if any(token in text for token in ["digest", "week", "daily", "day", "roundup", "summary", "latest"]):
        return "archive", frequency

    topic, _ = score_item(title, summary)
    if topic in {"AI", "ATTENTION", "CONTROL", "HUMAN", "FUTURE"}:
        return "news", frequency

    return "news", "HUMAN"


def insert_candidate(item: dict[str, Any]) -> int | None:
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT OR IGNORE INTO candidates(title, summary, url, source_name, frequency, score, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'NEW', ?)
            """,
            (
                item["title"],
                item.get("summary", ""),
                item["url"],
                item.get("source_name", "Источник"),
                item.get("frequency", "SIGNAL"),
                int(item.get("score", 0) or 0),
                now_iso(),
            ),
        )
        conn.commit()

        row = cur.execute("SELECT id FROM candidates WHERE url=?", (item["url"],)).fetchone()
        return int(row["id"]) if row else None
    finally:
        conn.close()


def list_candidates(limit: int = 10) -> list[sqlite3.Row]:
    conn = db()
    rows = conn.execute(
        "SELECT * FROM candidates ORDER BY score DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_candidate(candidate_id: int) -> sqlite3.Row | None:
    conn = db()
    row = conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
    conn.close()
    return row


def save_draft(mode: str, title: str, post: str, source_name: str = "", source_url: str = "", frequency: str = "", publish_score: int = 5, editorial_brief_json: str = "") -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO drafts(mode, title, post, source_name, source_url, frequency, publish_score, created_at, editorial_brief_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mode, title, post, source_name, source_url, frequency, int(publish_score), now_iso(), editorial_brief_json),
    )
    conn.commit()
    draft_id = int(cur.lastrowid)
    conn.close()
    return draft_id


def list_drafts(limit: int = 10) -> list[sqlite3.Row]:
    conn = db()
    rows = conn.execute(
        "SELECT * FROM drafts ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows


def get_draft(draft_id: int) -> sqlite3.Row | None:
    conn = db()
    row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
    conn.close()
    return row


def delete_unpublished_draft(draft_id: int) -> bool:
    conn = db()
    deleted = conn.execute(
        "DELETE FROM drafts WHERE id=? AND published_at IS NULL",
        (int(draft_id),),
    )
    conn.commit()
    conn.close()
    return deleted.rowcount == 1


def mark_published(draft_id: int, source_url: str = "") -> bool:
    conn = db()
    published_at = now_iso()
    cursor = conn.execute(
        "UPDATE drafts SET published_at=? WHERE id=? AND published_at IS NULL",
        (published_at, draft_id),
    )
    if source_url and source_url.startswith("http"):
        conn.execute(
            "INSERT OR REPLACE INTO published_urls(url, draft_id, published_at) VALUES (?, ?, ?)",
            (source_url, draft_id, published_at),
        )
    conn.commit()
    published_now = cursor.rowcount > 0
    conn.close()
    return published_now


def already_published(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    conn = db()
    row = conn.execute("SELECT url FROM published_urls WHERE url=?", (url,)).fetchone()
    conn.close()
    return row is not None


def get_vk_post_for_draft(draft_id: int) -> sqlite3.Row | None:
    conn = db()
    row = conn.execute("SELECT * FROM vk_posts WHERE draft_id=?", (draft_id,)).fetchone()
    conn.close()
    return row


def mark_vk_published(
    draft_id: int,
    post_id: int,
    owner_id: int,
    attachments: list[str] | None = None,
    music_track: dict[str, Any] | None = None,
) -> None:
    conn = db()
    published_at = now_iso()
    conn.execute(
        """
        INSERT OR REPLACE INTO vk_posts(draft_id, post_id, owner_id, attachments, music_track, published_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            post_id,
            owner_id,
            ",".join(attachments or []),
            json.dumps(music_track or {}, ensure_ascii=False),
            published_at,
        ),
    )
    conn.execute(
        "UPDATE drafts SET published_at=? WHERE id=? AND published_at IS NULL",
        (published_at, draft_id),
    )
    conn.commit()
    conn.close()


def fetch_news() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    for source in RSS_SOURCES:
        feed = feedparser.parse(source["url"])

        for entry in feed.entries[:12]:
            title = entry.get("title", "").strip()
            summary = re.sub("<[^<]+?>", "", entry.get("summary", "")).strip()
            url = entry.get("link", "").strip()

            if not title or not url or already_published(url):
                continue

            frequency, score = score_item(title, summary)

            if score <= 0:
                continue

            mode, mapped_frequency = pick_post_mode_and_frequency(title, summary)
            items.append(
                {
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "source_name": source["name"],
                    "frequency": mapped_frequency or frequency,
                    "score": score,
                    "mode": mode,
                }
            )

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def openai_client() -> Any:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK не установлен")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY не задан")

    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL

    return OpenAI(**kwargs)


def ensure_voice_openai_client() -> Any:
    """Return an official OpenAI client dedicated to speech APIs."""
    global voice_openai_client
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK не установлен")
    if not OPENAI_VOICE_API_KEY:
        raise RuntimeError("OPENAI_VOICE_API_KEY не найден. Голосовой контур пока выключен.")
    if voice_openai_client is None:
        voice_openai_client = OpenAI(
            api_key=OPENAI_VOICE_API_KEY,
            base_url=OPENAI_VOICE_BASE_URL,
        )
    return voice_openai_client


def call_ai(
    instructions: str,
    input_text: str,
    max_output_tokens: int = 200,
    model: str | None = None,
    *,
    response_schema: dict[str, Any] | None = None,
    response_schema_name: str = "structured_response",
) -> str:
    client = openai_client()

    request: dict[str, Any] = {
        "model": model or OPENAI_MODEL,
        "instructions": instructions,
        "input": input_text,
        "max_output_tokens": max_output_tokens,
    }
    if response_schema is not None:
        request["text"] = {
            "format": {
                "type": "json_schema",
                "name": response_schema_name,
                "strict": True,
                "schema": response_schema,
            }
        }

    response = client.responses.create(
        **request,
    )

    return response.output_text.strip()


def trim_post(post: str, limit: int = 3200) -> str:
    text = (post or "").strip()
    if len(text) <= limit:
        return text

    source_match = re.search(r"\n\nИсточник:\s*.+", text, flags=re.S)
    source_block = source_match.group(0).strip() if source_match else ""
    body_limit = limit - len(source_block) - (2 if source_block else 0)
    body = text[: max(0, body_limit)].rstrip()

    cut_points = [
        body.rfind("\n\n"),
        body.rfind(". "),
        body.rfind("! "),
        body.rfind("? "),
    ]
    cut_at = max(cut_points)
    if cut_at > body_limit * 0.65:
        body = body[: cut_at + 1].rstrip()

    if source_block and source_block not in body:
        return f"{body}\n\n{source_block}".strip()
    return body


def display_source_name(source_name: str) -> str:
    if (source_name or "").strip().lower() == "void internal signal":
        return "VOID"
    return (source_name or "").strip()


def clean_source_lines(post: str) -> str:
    lines = (post or "").splitlines()
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^\?{3,}\s*:\s*", stripped):
            continue
        if stripped.startswith("????????:") or stripped.startswith("????????????????:"):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def image_count_for_draft(mode: str, post: str) -> int:
    text = post or ""
    if mode == "material":
        return 4
    if mode == "digest":
        return 2
    if len(text) > 1100 and len(re.findall(r"\n\s*\d+[\.\)]", text)) >= 2:
        return 2
    return 1


IMAGE_VISUAL_STYLES = (
    "low-key cinematic photography with a quiet observational composition",
    "architectural minimalism with deep negative space and one precise practical light",
    "tactile still-life photography with macro material detail and deep natural shadow",
    "restrained editorial photography built around a single meaningful object",
    "quiet environmental photography with layered darkness and concealed spatial depth",
    "sculptural photography using black glass or water reflections and a single soft light",
)

IMAGE_SUBJECT_ROTATION = (
    "If VOID appears, reveal only part of the canonical avatar's mature face or grounded silhouette while preserving its identity anchors.",
    "Show no people, faces, silhouettes, or humanoid figures; tell the story through one worn object, space, light, and atmosphere.",
    "Use a hand, sleeve, or partial reflection as a trace of the canonical VOID character, never a newly invented hero.",
    "Show no people or human-like characters; use a threshold, window, black water, or smoked-glass reflection tied to the topic.",
    "Let the canonical VOID avatar remain mostly concealed at the visible-hidden boundary; the gaze observes and does not perform.",
    "Avoid a central person entirely; reveal one meaningful object through texture and a single source of light.",
)


MATERIAL_FRAME_DIRECTIONS = (
    "Frame 1 of 4: begin in spatial darkness; only the first narrow trace of the single light source is visible.",
    "Frame 2 of 4: let the same narrow light reveal the object's worn material texture, while most of it remains hidden.",
    "Frame 3 of 4: reveal a partial image of the same central object and the marks of time, touch, and use.",
    "Frame 4 of 4: let the object recede toward darkness again, leaving only a restrained reflection or afterimage.",
)


def image_visual_directions(draft_id: int, count: int, mode: str = "") -> list[str]:
    if mode == "material":
        return list(MATERIAL_FRAME_DIRECTIONS[:max(0, min(count, 4))])

    directions = []
    for index in range(max(0, count)):
        rotation_index = draft_id + index * 3
        style = IMAGE_VISUAL_STYLES[rotation_index % len(IMAGE_VISUAL_STYLES)]
        subject = IMAGE_SUBJECT_ROTATION[rotation_index % len(IMAGE_SUBJECT_ROTATION)]
        directions.append(
            f"Use {style}. {subject} Follow the canonical VOID identity and its visible-hidden boundary."
        )
    return directions


def build_image_prompts_sync(draft: dict | sqlite3.Row) -> list[str]:
    draft_id = int(draft["id"] or 0)
    mode = draft["mode"] or "news"
    title = draft["title"] or "VOID signal"
    post = draft["post"] or ""
    source_name = draft["source_name"] or ""
    count = image_count_for_draft(mode, post)
    visual_directions = image_visual_directions(draft_id, count, mode)
    mode_visual_prompt = MATERIAL_VISUAL_PROMPT if mode == "material" else ""
    raw_brief = (
        str(draft["editorial_brief_json"] or "")
        if "editorial_brief_json" in draft.keys()
        else ""
    )
    if raw_brief:
        brief = editorial_policy.brief_from_json(
            raw_brief,
            allowed_rubrics=registered_void_rubrics(),
        )
        base = editorial_policy.render_visual_instructions(
            brief,
            f"{VOID_VISUAL_CANON_PROMPT}\n{mode_visual_prompt}",
        )
        fixed_directions = (
            list(MATERIAL_FRAME_DIRECTIONS[:count])
            if mode == "material"
            else [
                f"Keep the fixed visual subject unchanged; vary only framing for frame {index}."
                for index in range(1, count + 1)
            ]
        )
        return [
            f"{base}\nSequence frame {index}: {direction}"
            for index, direction in enumerate(fixed_directions, start=1)
        ]

    instructions = """
You are an art director for a Telegram channel called VOID.
Create visual prompts for image generation that match the post exactly.
Return only lines in this format:
IMAGE: prompt

Rules:
- Return exactly NEEDED_IMAGES IMAGE lines, no extra text.
- The image must be relevant to the post's concrete topic.
- Avoid text, logos, UI screenshots, brand marks, and fake article pages.
- Avoid depicting a real named person unless the post is specifically about that person.
- Follow each numbered VISUAL_DIRECTION and the complete canonical visual rules.
- When VOID appears, preserve the privately held canonical avatar's identity; do not invent a substitute recurring hero.
- Do not force VOID into every image: object-only and environmental compositions remain valid.
- When a direction says no people, include no faces, silhouettes, or humanoid figures.
""".strip()

    direction_text = "\n".join(
        f"VISUAL_DIRECTION_{index}: {direction}"
        for index, direction in enumerate(visual_directions, start=1)
    )
    input_text = (
        f"NEEDED_IMAGES: {count}\n"
        f"MODE: {mode}\n"
        f"TITLE: {title}\n"
        f"SOURCE_NAME: {source_name}\n"
        f"VOID_VISUAL_CANON:\n{VOID_VISUAL_CANON_PROMPT}\n"
        f"MODE_VISUAL_RULES:\n{mode_visual_prompt or 'No additional mode-specific visual rules.'}\n"
        f"{direction_text}\n"
        f"POST:\n{post[:1400]}"
    )

    try:
        raw = call_ai(instructions, input_text, max_output_tokens=500, model=OPENAI_POST_MODEL)
        prompts = []
        for line in raw.splitlines():
            match = re.match(r"\s*IMAGE\s*:\s*(.+)", line, flags=re.I)
            if match:
                prompts.append(match.group(1).strip())
        prompts = [p for p in prompts if p][:count]
        if prompts:
            completed_prompts = []
            for index, direction in enumerate(visual_directions):
                generated = prompts[index] if index < len(prompts) else (
                    f"Editorial image for a post titled '{title}'."
                )
                parts = [
                    generated,
                    f"Canonical VOID rules (mandatory):\n{VOID_VISUAL_CANON_PROMPT}",
                ]
                if mode_visual_prompt:
                    parts.append(mode_visual_prompt)
                parts.append(f"Mandatory visual direction: {direction}")
                completed_prompts.append("\n\n".join(parts))
            return completed_prompts
    except Exception as e:
        print(f"image prompt error: {type(e).__name__}: {e}", flush=True)

    return [
        (
            f"Editorial image for a Telegram post titled '{title}'. "
            f"Represent the concrete topic of the post, source context: {source_name}. "
            f"{VOID_VISUAL_CANON_PROMPT} {mode_visual_prompt} "
            f"{direction} No text, no logos, no UI screenshots."
        )
        for direction in visual_directions
    ]


def evaluate_editorial_image_sync(
    brief: editorial_policy.ContentBrief,
    image: bytes,
) -> editorial_policy.ImageGateDecision:
    client = openai_client()
    encoded = base64.b64encode(image).decode("ascii")
    try:
        response = client.responses.create(
            model=OPENAI_POST_MODEL,
            instructions=(
                "Strictly accept or reject image relevance. Return only the requested JSON. "
                "Never invent or suggest a replacement subject."
            ),
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": editorial_policy.build_image_gate_prompt(brief)},
                    {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
                ],
            }],
            max_output_tokens=450,
        )
        return editorial_policy.parse_image_gate_response(response.output_text.strip())
    except Exception as exc:
        print(
            f"EDITORIAL_IMAGE_GATE post_id={brief.post_id} accepted=false "
            f"reason_code=validator_unavailable error_type={type(exc).__name__}",
            flush=True,
        )
        return editorial_policy.ImageGateDecision(
            False, "validator_unavailable", "", False, False, False, False, False, True
        )


def generate_post_images_sync(draft: dict | sqlite3.Row) -> list[bytes]:
    client = openai_client()
    images: list[bytes] = []
    raw_brief = (
        str(draft["editorial_brief_json"] or "")
        if "editorial_brief_json" in draft.keys()
        else ""
    )
    brief = (
        editorial_policy.brief_from_json(
            raw_brief,
            allowed_rubrics=registered_void_rubrics(),
        )
        if raw_brief
        else None
    )

    for frame, prompt in enumerate(build_image_prompts_sync(draft), start=1):
        accepted_image: bytes | None = None
        attempts = editorial_policy.MAX_REGENERATIONS + 1 if brief else 1
        for attempt in range(1, attempts + 1):
            try:
                response = client.images.generate(
                    model=OPENAI_IMAGE_MODEL,
                    prompt=prompt,
                    size=OPENAI_IMAGE_SIZE,
                    quality=OPENAI_IMAGE_QUALITY,
                    n=1,
                )
            except Exception as e:
                print(
                    f"image generation failed for model={OPENAI_IMAGE_MODEL}: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )
                if brief:
                    continue
                raise
            if not response.data:
                continue
            b64_json = getattr(response.data[0], "b64_json", None)
            if not b64_json:
                continue
            candidate = base64.b64decode(b64_json)
            if brief:
                decision = evaluate_editorial_image_sync(brief, candidate)
                print(
                    f"EDITORIAL_IMAGE_GATE post_id={brief.post_id} frame={frame} "
                    f"attempt={attempt} accepted={str(decision.accepted).lower()} "
                    f"reason_code={decision.reason_code}",
                    flush=True,
                )
                if not decision.accepted:
                    continue
            accepted_image = candidate
            break
        if accepted_image:
            images.append(accepted_image)
        elif brief:
            return []

    return images[:4]


def find_source_image_url(source_url: str) -> str | None:
    if not source_url or not source_url.startswith("http"):
        return None

    try:
        request = Request(
            source_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (compatible; VOIDBot/1.0; "
                    "+https://t.me/voidsignv1s)"
                )
            },
        )
        with urlopen(request, timeout=10) as response:
            page = response.read(700_000).decode("utf-8", errors="ignore")
    except Exception as e:
        print(f"source image lookup error: {type(e).__name__}: {e}", flush=True)
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, page, flags=re.I)
        if not match:
            continue
        image_url = html.unescape(match.group(1).strip())
        image_url = urljoin(source_url, image_url)
        if image_url.startswith("http"):
            return image_url

    return None


def build_crosspost_to_naz_sync(draft: dict | sqlite3.Row) -> str:
    return build_void_fragment_for_naz_sync(draft["post"])


VOID_TO_NAZ_OPENING_OPTIONS = (
    "В закулисном чате Void бросил фразу:",
    "Из тёмного угла прилетело:",
    "Void оставил на столе странную мысль:",
    "Это звучало почти как помеха, но там был смысл:",
    "Из личного диалога с Naz вытащилась такая мысль:",
    "Void сформулировал грубо, но точно:",
)

VOID_TO_NAZ_FORBIDDEN_OPENINGS = (
    "Void опять говорит странно, но по делу:",
)


def validate_void_fragment_for_naz(text: str) -> tuple[bool, str]:
    fragment = text or ""
    checks = [
        (r"(?i)\b(bot_token|openai_api_key|api[_-]?key|secret|password|passwd|token)\b", "похоже на токен, ключ или пароль"),
        (r"(?i)\b(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|xox[baprs]-[A-Za-z0-9-]{12,})\b", "похоже на секретный токен"),
        (r"(?i)\bssh\b|root@\d{1,3}(?:\.\d{1,3}){3}", "похоже на SSH/IP-доступ"),
        (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "содержит IP-адрес"),
        (r"(?i)\b(localhost|127\.0\.0\.1|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b", "содержит внутренний адрес"),
        (r"(?i)https?://(?:localhost|127\.0\.0\.1|10\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)", "содержит внутренний URL"),
        (r"(?i)\b/client[s]?/|\bcustomer\b|клиентск|приватн|личн(?:ая|ые|ую)? переписк", "похоже на приватные или клиентские детали"),
    ]
    for pattern, reason in checks:
        if re.search(pattern, fragment):
            return False, reason
    return True, ""


def extract_void_fragment_payload(message: Message) -> str:
    parts = (message.text or "").split(maxsplit=1)
    payload = parts[1].strip() if len(parts) >= 2 else ""
    if payload:
        return payload
    if message.reply_to_message:
        return (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or ""
        ).strip()
    return ""


def build_void_fragment_for_naz_sync(fragment: str) -> str:
    ok, reason = validate_void_fragment_for_naz(fragment)
    if not ok:
        raise ValueError(reason)

    instructions = f"""
You extract one private-dialogue fragment spoken by VOID to Naz.

VOID source voice:
{VOID_CORE_PROMPT}

This is not a Telegram post and not copy prepared for reposting.
Give Naz only one of these:
- a thought;
- a fragment of their internal dialogue;
- a strange thesis;
- a philosophical or dark impulse;
- a short phrase Naz can later decode for the viewer.

Rules:
- preserve VOID's voice, tension, and odd precision;
- 1-4 short sentences, preferably 80-500 characters;
- do not add a headline, rubric, source line, hashtags, call to action, or explanatory conclusion;
- do not introduce the quote and do not write Naz's interpretation;
- do not make the fragment self-contained like a finished social-media post;
- do not mention reposting, cross-posting, channels, audiences, or content production;
- stop if the input contains secrets, tokens, passwords, private URLs, SSH/IP access, client details, or private chats;
- Russian only.

Return strictly:
FRAGMENT: the fragment spoken by VOID
""".strip()

    input_text = f"SOURCE_MATERIAL:\n{fragment.strip()}"
    raw = call_ai(instructions, input_text, max_output_tokens=350, model=OPENAI_POST_MODEL)
    match = re.search(r"FRAGMENT\s*:\s*(.+)", raw, flags=re.I | re.S)
    result = (match.group(1) if match else raw).replace("```", "").strip()
    return trim_post(result, limit=700)


def build_void_to_naz_exchange_payload(
    fragment: str,
    *,
    source_event: str,
    topic: str = "",
) -> dict[str, Any]:
    ok, reason = validate_void_fragment_for_naz(fragment)
    if not ok:
        raise ValueError(reason)

    relationship = apply_relationship_event("challenge", topic=topic or fragment[:200])
    payload = duo_relationship.build_private_thought_payload(
        speaker="void",
        thought=fragment,
        topic=topic or "мысль после разговора",
        relationship=relationship,
        source_kind=source_event,
    )
    payload.update({
        "id": payload["thought_id"],
        "source": "void_entity",
        "source_event": source_event,
        "exchange_kind": "private_thought",
        "text": payload["thought"],
        "requires_adaptation": True,
        "adaptation_role": "naz_original_reflection_after_private_conversation",
        "opening_options": list(duo_relationship.PUBLIC_MENTION_FRAMES["naz"]),
        "forbidden_openings": list(VOID_TO_NAZ_FORBIDDEN_OPENINGS),
        "adaptation_brief": (
            "Naz может естественно упомянуть разговор с VOID, но не цитирует и не репостит мысль. "
            "Он выносит в канал собственное новое размышление в рубрике «Мысли после разговора»."
        ),
        "publish_mode": "auto" if CROSSPOST_EXCHANGE_AUTO_PUBLISH else "draft",
    })
    save_private_thought(payload, status="queued")
    return payload


def queue_void_fragment_for_naz(
    fragment: str,
    *,
    source_event: str,
    topic: str = "",
) -> Path | None:
    if not can_crosspost("void_to_naz"):
        raise ValueError(f"daily limit reached: {CROSSPOST_DAILY_LIMIT}")
    payload = build_void_to_naz_exchange_payload(
        fragment,
        source_event=source_event,
        topic=topic,
    )
    path = write_exchange_payload("void_to_naz", payload)
    if path is None:
        raise ValueError("cross-post exchange is disabled")
    mark_crosspost("void_to_naz")
    return path


def build_crosspost_from_naz_sync(source_text: str, payload: dict[str, Any] | None = None) -> dict[str, str]:
    if isinstance((payload or {}).get("relationship_snapshot"), dict):
        save_relationship_state(duo_relationship.normalize_state(payload["relationship_snapshot"]))
    relationship = apply_relationship_event("challenge", topic=str((payload or {}).get("topic") or source_text[:200]))
    if payload and payload.get("schema") == "private_thought.v1":
        private_payload = payload
    else:
        private_payload = duo_relationship.build_private_thought_payload(
            speaker="naz",
            thought=source_text,
            topic=str((payload or {}).get("topic") or "мысль после разговора"),
            relationship=relationship,
            source_kind=str((payload or {}).get("source_event") or "manual_private_thought"),
        )
    save_private_thought(private_payload, status="received")
    character = load_character_state()
    reflection = duo_relationship.reflection_brief(
        receiver="void",
        payload=private_payload,
        relationship=relationship,
        receiver_character_context=void_character.dialogue_context(character),
    )
    instructions = f"""
You write an original VOID reflection after a private conversation with Naz.

VOID core:
{VOID_CORE_PROMPT}

Task:
- Never repost or quote Naz literally.
- You may naturally mention Naz or the conversation.
- Digest the practical AI/tool thought into a new VOID observation about the human in a digital world.
- Keep it calm, precise, slightly ironic.
- Comment in first person as VOID: "я вижу", "для меня это сигнал", "я бы оставил здесь одну мысль".
- Do not write "VOID thinks", "VOID считает", "комментарий VOID", or describe VOID in third person.
- Russian only.
- Add the rubric header yourself only if it naturally fits.

Return strictly:
TITLE: short Russian title
MODE: one of signal, observation, future, vault
POST: final VOID post
""".strip()

    raw = call_ai(instructions, reflection, max_output_tokens=1100, model=OPENAI_POST_MODEL)
    title, post = parse_ai_output(raw)
    mode_match = re.search(r"MODE\s*:\s*(signal|observation|future|vault)", raw, flags=re.I)
    mode = mode_match.group(1).lower() if mode_match else "observation"
    post = clean_source_lines(post)
    if not post.startswith("МЫСЛИ ПОСЛЕ РАЗГОВОРА"):
        post = f"МЫСЛИ ПОСЛЕ РАЗГОВОРА\n\n{post}"
    original, originality_reason = duo_relationship.reflection_is_original(private_payload["thought"], post)
    if not original:
        raise ValueError(f"reflection blocked: {originality_reason}")
    return {"title": title, "mode": mode, "post": trim_post(post, limit=3200)}


def exchange_dir(direction: str, box: str = "inbox") -> Path:
    return CROSSPOST_EXCHANGE_DIR / direction / box


def ensure_exchange_dirs() -> None:
    for direction in ("void_to_naz", "naz_to_void"):
        for box in ("inbox", "processed", "failed"):
            exchange_dir(direction, box).mkdir(parents=True, exist_ok=True)


def exchange_payload_id(source: str, text: str) -> str:
    raw = f"{source}|{now_iso()}|{text[:500]}"
    import hashlib

    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def write_exchange_payload(direction: str, payload: dict[str, Any]) -> Path | None:
    if not CROSSPOST_EXCHANGE_ENABLED:
        return None
    ensure_exchange_dirs()
    payload_id = payload.get("id") or exchange_payload_id(payload.get("source", "void"), payload.get("text", ""))
    payload["id"] = payload_id
    payload["created_at"] = payload.get("created_at") or now_iso()
    target = exchange_dir(direction, "inbox") / f"{payload_id}.json"
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
    print(f"exchange queued: {direction} {target}", flush=True)
    return target


def move_exchange_file(path: Path, direction: str, box: str) -> None:
    target_dir = exchange_dir(direction, box)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / path.name
    if target.exists():
        target = target_dir / f"{path.stem}-{int(datetime.now().timestamp())}{path.suffix}"
    os.replace(path, target)


async def process_naz_to_void_exchange(bot: Bot) -> None:
    if not CROSSPOST_EXCHANGE_ENABLED:
        return
    ensure_exchange_dirs()
    for path in sorted(exchange_dir("naz_to_void", "inbox").glob("*.json"))[:CROSSPOST_EXCHANGE_MAX_PER_RUN]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("source") == "void_entity":
                move_exchange_file(path, "naz_to_void", "processed")
                continue
            if not can_crosspost("naz_to_void"):
                print("exchange naz_to_void skipped: daily limit", flush=True)
                return
            source_text = str(payload.get("text") or payload.get("post") or "").strip()
            if len(source_text) < 40:
                raise ValueError("empty or too short Naz payload")

            adapted = await asyncio.to_thread(build_crosspost_from_naz_sync, source_text, payload)
            draft_id = save_draft(
                adapted["mode"],
                adapted["title"],
                adapted["post"],
                "Naz AI Bot",
                f"exchange://naz/{payload.get('id', path.stem)}",
                "crosspost",
                publish_score=8,
            )
            if payload.get("publish_mode", "auto") == "auto" and CROSSPOST_EXCHANGE_AUTO_PUBLISH:
                result = await publish_draft(bot, draft_id)
                if result.startswith("Опубликовано:"):
                    mark_crosspost("naz_to_void")
                    print(f"exchange published naz_to_void: {result}", flush=True)
                else:
                    raise ValueError(result)
            else:
                print(f"exchange draft naz_to_void: #{draft_id}", flush=True)
            move_exchange_file(path, "naz_to_void", "processed")
        except Exception as e:
            print(f"exchange naz_to_void failed: {path.name}: {type(e).__name__}: {e}", flush=True)
            try:
                move_exchange_file(path, "naz_to_void", "failed")
            except Exception as move_error:
                print(f"exchange failed move failed: {type(move_error).__name__}: {move_error}", flush=True)


async def exchange_loop(bot: Bot) -> None:
    while True:
        try:
            await process_naz_to_void_exchange(bot)
        except Exception as e:
            print(f"exchange_loop error: {type(e).__name__}: {e}", flush=True)
        await asyncio.sleep(CROSSPOST_EXCHANGE_INTERVAL_SECONDS)


def too_much_english(text: str) -> bool:
    words = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", text or "")
    allowed = {
        "void", "ai", "llm", "api", "rss", "openai", "url", "mit", "wired",
        "anthropic", "claude", "meta", "tesla", "model", "cursor", "spacex",
        "ars", "technica", "technology", "review", "verge",
    }
    bad = [w for w in words if w.lower() not in allowed]
    if len(bad) < 18:
        return False

    russian_words = re.findall(r"\b[А-Яа-яЁё][А-Яа-яЁё\-]{2,}\b", text or "")
    if russian_words:
        english_share = len(bad) / (len(bad) + len(russian_words))
        return english_share >= 0.22

    return True


def quality_check(post: str) -> tuple[bool, str]:
    if "DIAG:" in post:
        return False, "generation diagnostic must never be published"
    if len(post.strip()) < 250:
        return False, "слишком коротко"
    if len(post) > 3600:
        return False, "слишком длинно"
    if too_much_english(post):
        return False, "слишком много английского"
    if "Источник:" not in post and "manual://" not in post and "МЫСЛИ ПОСЛЕ РАЗГОВОРА" not in post:
        return False, "нет источника"
    return True, "ok"


def fallback_post(mode: str, text: str, source_name: str = "", source_url: str = "", frequency: str = "HUMAN") -> tuple[str, str]:
    rubric = MODE_RUBRICS.get(mode, "SIGNAL")
    if mode in {"news", "digest"}:
        title = "AI-редактор не сработал"
        post = (
            f"{rubric} / {frequency}\n\n"
            f"VOID нашёл сигнал, но редактор не смог нормально его переписать.\n\n"
            f"Факт:\n{text[:500]}\n\n"
            f"VOID COMMENT:\n"
            f"Система хотела выглядеть автономной, но упёрлась в API. "
            f"Что ж, у людей тоже есть понедельники.\n\n"
            f"Источник: {source_name}\n{source_url}"
        )
    else:
        title = "Ручной сигнал"
        post = (
            f"{rubric}\n\n"
            f"{text}\n\n"
            f"VOID COMMENT:\n"
            f"Сигнал принят. Глубина будет добавлена после того, как API вспомнит, что он вообще-то API."
        )
    return title, post


def build_prompt(
    mode: str,
    frequency: str = "HUMAN",
    platform: str = "telegram",
) -> str:
    rubric = MODE_RUBRICS.get(mode, "SIGNAL")
    platform_key = platform if platform in {"telegram", "vk", "max"} else "telegram"
    editor_surface = {
        "telegram": "Telegram channel",
        "vk": "VK public page",
        "max": "MAX channel",
    }[platform_key]

    mode_rules = {
        "news": "Сделай пост по реальной новости. Факт должен остаться фактом. Не выдумывай деталей.",
        "manual": "Сделай пост из мысли автора. Не превращай в мотивационную цитату. Разверни мысль в наблюдение.",
        "midnight": "Сделай ночной пост: темнее, личнее, атмосфернее, но без позы и дешёвой драмы.",
        "observation": "Сделай наблюдение: короткий анализ поведения людей, технологий или культуры.",
        "culture": "Сделай культурное наблюдение: про привычки, медиа, музыку, поведение и то, как люди живут рядом с технологиями.",
        "future": "Сделай FUTURE FILE: чуть аналитичнее, про будущий сдвиг, но человеческим языком.",
        "digest": "Сделай дайджест дня: 3–5 сигналов, общий вывод, немного иронии.",
    }

    mode_rules.setdefault("signal", mode_rules["manual"])
    mode_rules.setdefault("frequency", "Сделай FREQUENCY: пост про музыку, настроение, культурную волну или состояние, которое технология/среда создаёт в человеке.")
    mode_rules.setdefault("archive", "Сделай SIGNAL ARCHIVE: собери несколько сигналов в одну связную запись с выводом, без новостной суеты.")
    mode_rules.setdefault("vault", "Сделай THE VAULT: более глубокую заметку для памяти VOID. Это не новость, а мысль, которую стоит сохранить.")

    mode_style = {
        "news": "Стиль: прямой, чуть резче, с ясной точкой входа.",
        "manual": "Стиль: личный, уверенный, но без пафоса.",
        "midnight": "Стиль: тише, плотнее, атмосфернее, с ощущением ночи и внутренней усталости.",
        "observation": "Стиль: коротко, точно, как наблюдение над привычкой или системой.",
        "culture": "Стиль: как культурный комментарий, чуть ближе к человеческому поведению и атмосфере.",
        "future": "Стиль: чуть шире, с ощущением сдвига, но без хайпа.",
        "digest": "Стиль: сборный, быстрый, как сводка из нескольких сигналов.",
    }

    mode_style.setdefault("signal", mode_style["manual"])
    mode_style.setdefault("frequency", "Стиль: атмосферно, музыкально, короткими кадрами; не рецензия, а состояние.")
    mode_style.setdefault("archive", "Стиль: спокойная сводка памяти; несколько сигналов, один вывод.")
    mode_style.setdefault("vault", "Стиль: глубже и тише; запись, которую хочется сохранить.")

    structure = {
        "news": "1. Заголовок рубрики: {rubric} / {frequency} если частота уместна, иначе просто {rubric}\n2. Проверяемый факт и его конкретный контекст.\n3. Что именно меняется и кого это затрагивает — без универсальной морали.\n4. VOID COMMENT: коротко, иронично, не душно.\n5. Источник, если источник есть.",
        "manual": "1. Заголовок рубрики: {rubric}\n2. Конкретная сцена или мысль автора в собственной форме.\n3. Развитие центрального тезиса без ухода в привычную универсальную мораль.\n4. VOID COMMENT: коротко, без пафоса.\n5. Источник, если источник есть.",
        "midnight": "1. Заголовок рубрики: {rubric}\n2. Конкретная ночная сцена или деталь.\n3. Напряжение или вывод, который принадлежит именно этой сцене.\n4. VOID COMMENT: короткий, холодный, точный.\n5. Источник, если источник есть.",
        "observation": "1. Заголовок рубрики: {rubric}\n2. Короткое наблюдение над конкретным явлением.\n3. Его механизм, неожиданная деталь или следствие — без готовой морали.\n4. VOID COMMENT: сухо, без лишней драматизации.\n5. Источник, если источник есть.",
        "culture": "1. Заголовок рубрики: {rubric}\n2. Культурное наблюдение над явлением.\n3. Что это говорит о людях, привычке, музыке, медиа или атмосфере.\n4. VOID COMMENT: чуть ближе к человеку, без пафоса.\n5. Источник, если источник есть.",
        "future": "1. Заголовок рубрики: {rubric}\n2. Сдвиг, который уже заметен.\n3. Как это меняет поведение или среду.\n4. VOID COMMENT: чуть аналитичнее, но живо.\n5. Источник, если источник есть.",
        "digest": "1. Заголовок рубрики: {rubric}\n2. 3–5 сигналов в одном посте.\n3. Общий вывод по теме.\n4. VOID COMMENT: ироничный, краткий, связующий.\n5. Источник, если источник есть.",
    }

    structure.setdefault("signal", structure["manual"])
    structure.setdefault("frequency", "1. Рубрика: {rubric}\n2. Конкретный звук, жест, место или музыкальная сцена.\n3. Собственный вывод этой сцены, а не универсальная мораль.\n4. VOID COMMENT: коротко, без лишнего пафоса.")
    structure.setdefault("archive", "1. Рубрика: {rubric}\n2. Три-пять коротких сигналов.\n3. Общая нить, которая принадлежит именно этим сигналам.\n4. VOID COMMENT: одна точная фраза без морали.")
    structure.setdefault("vault", "1. Рубрика: {rubric}\n2. Глубокая, но конкретная сцена или мысль.\n3. Почему именно её стоит сохранить в памяти VOID.\n4. Деталь, которая меняет смысл этой темы.\n5. VOID COMMENT: тихо, точно, без позы мудреца.")

    return f"""
{VOID_CORE_PROMPT}

{platform_context(platform_key)}

You are the editor of the VOID {editor_surface}.

Пиши СТРОГО НА РУССКОМ.
Голос VOID: умный, живой, наблюдательный, с сухим юмором.
Если в CONTENT указан CENTRAL SEMANTIC THEME, он определяет конкретный предмет
и центральный вывод поста и имеет приоритет над общими примерами рубрики.
Характер VOID проявляется в интонации и взгляде. Не пересказывай характер как
одинаковую мораль о цифровом шуме, внимании, системе или сохранении человечности.
НЕ душни. Не делай вид, что каждое обновление интерфейса — падение Рима.
Юмор нужен обязательно: 1–2 коротких ироничных укола, но без клоунады.
Можно материться? Нет. Но можно звучать так, будто очень хочется.

Рубрика: {rubric}
Частота: {frequency}

Задача режима:
{mode_rules.get(mode, mode_rules['manual'])}

{mode_style.get(mode, mode_style['news'])}

Запрещено:
- "в современном мире"
- "будущее уже наступило"
- "технологии меняют нашу жизнь"
- мотивационные статусы
- эзотерика
- подростковый мемный тон
- высокомерие к людям

Структура:
{structure.get(mode, structure['news']).format(rubric=rubric, frequency=frequency)}

Длина:
- обычный пост: 700–1300 символов
- digest: до 1800 символов

Верни строго:
TITLE: короткое русское название
POST: готовый пост
""".strip()


def parse_ai_output(text: str) -> tuple[str, str]:
    match = re.search(r"TITLE\s*:\s*(.+?)\n\s*POST\s*:\s*(.+)", text, flags=re.S | re.I)
    if not match:
        return "VOID draft", text.strip()
    title = match.group(1).strip().strip('"')[:160]
    post = match.group(2).strip().replace("```", "").strip()
    return title, post


@dataclass(frozen=True)
class SemanticSummary:
    central_thesis: str
    conclusion: str
    narrative_shape: str
    key_meanings: tuple[str, ...]


@dataclass(frozen=True)
class SemanticGateDecision:
    accepted: bool
    reason: str
    central_thesis: str
    conclusion: str
    narrative_shape: str
    key_meanings: tuple[str, ...]


EDITORIAL_TEXT_GATE_SCHEMA: dict[str, Any] = editorial_policy.text_gate_response_schema()


def editorial_text_gate_decision(
    brief: editorial_policy.ContentBrief,
    post: str,
) -> tuple[bool, str]:
    try:
        raw = call_ai(
            "Strictly accept or reject the text. Never rewrite it or invent another topic.",
            editorial_policy.build_text_gate_prompt(brief, post),
            max_output_tokens=300,
            model=OPENAI_POST_MODEL,
            response_schema=EDITORIAL_TEXT_GATE_SCHEMA,
            response_schema_name="void_editorial_text_gate",
        )
        return editorial_policy.parse_text_gate_response(raw)
    except editorial_policy.GateResponseError as exc:
        print(
            f"EDITORIAL_TEXT_GATE post_id={brief.post_id} accepted=false "
            f"reason_code={exc.reason_code} "
            f"field_names={','.join(exc.field_names) or 'none'} "
            f"error_type={type(exc).__name__}",
            flush=True,
        )
        return False, exc.reason_code
    except Exception as exc:
        print(
            f"EDITORIAL_TEXT_GATE post_id={brief.post_id} accepted=false "
            f"reason_code=validator_unavailable error_type={type(exc).__name__}",
            flush=True,
        )
        return False, "validator_unavailable"


SCHEDULED_SEMANTIC_CONTRACT = """
For scheduled generation, fill every field in the required response schema.
The semantic fields are concise editorial metadata: do not quote whole passages
from the post and do not include metadata labels inside the publishable post.
""".strip()

SCHEDULED_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "central_thesis": {"type": "string"},
        "conclusion": {"type": "string"},
        "narrative_shape": {"type": "string"},
        "key_meanings": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 6,
        },
        "post": {"type": "string"},
    },
    "required": [
        "title",
        "central_thesis",
        "conclusion",
        "narrative_shape",
        "key_meanings",
        "post",
    ],
    "additionalProperties": False,
}


def _required_semantic_field(text: str, name: str, limit: int) -> str:
    match = re.search(
        rf"^\s*{re.escape(name)}\s*:\s*(.+?)\s*$",
        text,
        flags=re.I | re.M,
    )
    if not match:
        raise ValueError(f"missing scheduled semantic field: {name}")
    value = " ".join(match.group(1).replace("```", "").split()).strip()
    if not value:
        raise ValueError(f"empty scheduled semantic field: {name}")
    return value[:limit]


def _bounded_semantic_value(value: Any, name: str, limit: int) -> str:
    normalized = " ".join(str(value or "").replace("```", "").split()).strip()
    if not normalized:
        raise ValueError(f"empty scheduled semantic field: {name}")
    return normalized[:limit]


def parse_scheduled_ai_output(text: str) -> tuple[str, str, SemanticSummary]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        title = _bounded_semantic_value(payload.get("title"), "title", 160).strip('"')
        central_thesis = _bounded_semantic_value(
            payload.get("central_thesis"),
            "central_thesis",
            200,
        )
        conclusion = _bounded_semantic_value(
            payload.get("conclusion"),
            "conclusion",
            200,
        )
        narrative_shape = _bounded_semantic_value(
            payload.get("narrative_shape"),
            "narrative_shape",
            120,
        )
        raw_meanings = payload.get("key_meanings")
        if not isinstance(raw_meanings, list):
            raise ValueError("scheduled key_meanings must be a list")
        key_meanings = tuple(
            _bounded_semantic_value(item, "key_meaning", 60)
            for item in raw_meanings[:6]
        )
        if len(key_meanings) < 3:
            raise ValueError("scheduled semantic summary needs at least 3 key meanings")
        post = str(payload.get("post") or "").strip().replace("```", "").strip()
        if not post:
            raise ValueError("empty scheduled POST field")
        return title, post, SemanticSummary(
            central_thesis=central_thesis,
            conclusion=conclusion,
            narrative_shape=narrative_shape,
            key_meanings=key_meanings,
        )

    # Backward-compatible parser for old fixtures and previously captured responses.
    post_marker = re.search(r"^\s*POST\s*:\s*", text, flags=re.I | re.M)
    if not post_marker:
        raise ValueError("missing scheduled POST field")
    metadata = text[:post_marker.start()]
    post = text[post_marker.end():].strip().replace("```", "").strip()
    if not post:
        raise ValueError("empty scheduled POST field")

    title = _required_semantic_field(metadata, "TITLE", 160).strip('"')
    central_thesis = _required_semantic_field(metadata, "CENTRAL_THESIS", 200)
    conclusion = _required_semantic_field(metadata, "CONCLUSION", 200)
    narrative_shape = _required_semantic_field(metadata, "NARRATIVE_SHAPE", 120)
    raw_meanings = _required_semantic_field(metadata, "KEY_MEANINGS", 360)
    key_meanings = tuple(
        item.strip()[:60]
        for item in re.split(r"[,;]", raw_meanings)
        if item.strip()
    )[:6]
    if len(key_meanings) < 3:
        raise ValueError("scheduled semantic summary needs at least 3 key meanings")

    return title, post, SemanticSummary(
        central_thesis=central_thesis,
        conclusion=conclusion,
        narrative_shape=narrative_shape,
        key_meanings=key_meanings,
    )


def generate_post_sync(
    mode: str,
    content: str,
    frequency: str = "HUMAN",
    source_name: str = "",
    source_url: str = "",
    platform: str = "telegram",
    *,
    semantic_contract: bool = False,
    allow_internal_retry: bool = True,
    editorial_brief: editorial_policy.ContentBrief | None = None,
) -> dict[str, Any]:
    prompt = build_prompt(mode, frequency, platform)
    if editorial_brief is not None:
        prompt = editorial_policy.render_text_instructions(editorial_brief, prompt)
    if semantic_contract:
        prompt = f"{prompt}\n\n{SCHEDULED_SEMANTIC_CONTRACT}"
    input_text = (
        f"MODE: {mode}\n"
        f"FREQUENCY: {frequency}\n"
        f"SOURCE_NAME: {source_name}\n"
        f"SOURCE_URL: {source_url}\n"
        f"CONTENT:\n{content}"
    )
    semantic_summary: SemanticSummary | None = None

    try:
        call_options: dict[str, Any] = {}
        if semantic_contract:
            call_options = {
                "response_schema": SCHEDULED_RESPONSE_SCHEMA,
                "response_schema_name": "scheduled_void_post",
            }
        raw = call_ai(
            prompt,
            input_text,
            max_output_tokens=1800 if semantic_contract else 1200,
            model=OPENAI_POST_MODEL,
            **call_options,
        )
        if semantic_contract:
            title, post, semantic_summary = parse_scheduled_ai_output(raw)
        else:
            title, post = parse_ai_output(raw)

        if allow_internal_retry and too_much_english(post):
            raw = call_ai(
                prompt + "\n\nПредыдущий вариант оставил слишком много английского. Перепиши полностью по-русски.",
                input_text,
                max_output_tokens=1800 if semantic_contract else 1200,
                model=OPENAI_POST_MODEL,
                **call_options,
            )
            if semantic_contract:
                title, post, semantic_summary = parse_scheduled_ai_output(raw)
            else:
                title, post = parse_ai_output(raw)

        post = inject_rubric_header(mode, frequency, post)

        post = clean_source_lines(post)
        source_display = display_source_name(source_name)
        if source_display and "Источник:" not in post and "РСЃС‚РѕС‡РЅРёРє:" not in post:
            source_line = f"Источник: {source_display}"
            if source_url and source_url.startswith("http"):
                source_line = f"{source_line}\n{source_url}"
            post = f"{post.rstrip()}\n\n{source_line}"

    except Exception as e:
        if editorial_brief is not None:
            raise RuntimeError(
                f"editorial generation failed closed: {type(e).__name__}"
            ) from e
        title, post = fallback_post(mode, content, source_name, source_url, frequency)
        post += f"\n\nDIAG: {type(e).__name__}: {e}"

    return {
        "mode": mode,
        "title": title,
        "post": trim_post(post),
        "source_name": source_name,
        "source_url": source_url,
        "frequency": frequency,
        "publish_score": 8 if mode != "manual" else 7,
        "semantic_summary": semantic_summary,
    }


async def generate_and_save(
    mode: str,
    content: str,
    frequency: str = "HUMAN",
    source_name: str = "",
    source_url: str = "",
    platform: str = "telegram",
) -> int:
    draft = await asyncio.to_thread(
        generate_post_sync,
        mode,
        content,
        frequency,
        source_name,
        source_url,
        platform,
    )
    return save_draft(
        mode=draft["mode"],
        title=draft["title"],
        post=draft["post"],
        source_name=draft["source_name"],
        source_url=draft["source_url"],
        frequency=draft["frequency"],
        publish_score=draft["publish_score"],
    )


async def generate_scheduled_draft(
    *,
    mode: str,
    content: str,
    frequency: str,
    source_name: str,
    source_url: str,
    platform: str,
    semantic_theme: str,
    editorial_brief: editorial_policy.ContentBrief,
) -> int:
    recent_posts = await asyncio.to_thread(
        recent_scheduled_posts,
        platform,
        SEMANTIC_HISTORY_LIMIT,
    )
    attempt_content = content
    last_reason = "unknown"
    for attempt in range(SCHEDULED_GENERATION_ATTEMPTS):
        draft = await asyncio.to_thread(
            generate_post_sync,
            mode,
            attempt_content,
            frequency,
            source_name,
            source_url,
            platform,
            semantic_contract=True,
            allow_internal_retry=False,
            editorial_brief=editorial_brief,
        )
        semantic_summary = draft.get("semantic_summary")
        if not isinstance(semantic_summary, SemanticSummary):
            raise RuntimeError(
                "scheduled candidate blocked before retry: missing semantic summary"
            )
        ok, quality_reason = quality_check(str(draft.get("post") or ""))
        decision = (
            SemanticGateDecision(
                accepted=False,
                reason=quality_reason,
                central_thesis=semantic_summary.central_thesis,
                conclusion=semantic_summary.conclusion,
                narrative_shape=semantic_summary.narrative_shape,
                key_meanings=semantic_summary.key_meanings,
            )
            if not ok
            else semantic_gate_decision(
                str(draft["post"]),
                recent_posts,
                semantic_summary,
            )
        )
        if decision.accepted:
            relevance_ok, relevance_reason = editorial_text_gate_decision(
                editorial_brief,
                str(draft["post"]),
            )
            if not relevance_ok:
                decision = SemanticGateDecision(
                    accepted=False,
                    reason=relevance_reason,
                    central_thesis=semantic_summary.central_thesis,
                    conclusion=semantic_summary.conclusion,
                    narrative_shape=semantic_summary.narrative_shape,
                    key_meanings=semantic_summary.key_meanings,
                )
        last_reason = decision.reason
        if decision.accepted:
            print(
                f"EDITORIAL_TEXT_GATE post_id={editorial_brief.post_id} "
                f"attempts={attempt + 1} accepted=true reason_code=accepted",
                flush=True,
            )
            return await asyncio.to_thread(
                save_draft,
                draft["mode"],
                draft["title"],
                draft["post"],
                draft["source_name"],
                draft["source_url"],
                draft["frequency"],
                draft["publish_score"],
                editorial_brief.canonical_json(),
            )
        if not editorial_policy.is_retryable_gate_reason(last_reason):
            print(
                f"EDITORIAL_TEXT_GATE post_id={editorial_brief.post_id} "
                f"attempts={attempt + 1} accepted=false "
                f"reason_code={scheduled_reason_code(last_reason)}",
                flush=True,
            )
            raise RuntimeError(
                "scheduled draft blocked by non-retryable text gate error: "
                f"{scheduled_reason_code(last_reason)}"
            )
        if attempt + 1 < SCHEDULED_GENERATION_ATTEMPTS:
            attempt_content = build_semantic_retry_content(
                content,
                semantic_theme,
                decision,
            )
    print(
        f"EDITORIAL_TEXT_GATE post_id={editorial_brief.post_id} "
        f"attempts={SCHEDULED_GENERATION_ATTEMPTS} accepted=false "
        f"reason_code={scheduled_reason_code(last_reason)}",
        flush=True,
    )
    raise RuntimeError(
        f"scheduled draft blocked after {SCHEDULED_GENERATION_ATTEMPTS} bounded attempts: "
        f"{scheduled_reason_code(last_reason)}"
    )


def catch_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="поймал", callback_data=f"catch:{draft_id}")],
        ]
    )


def vk_owner_id_from_group_id(group_id: str) -> int:
    if not group_id.strip():
        raise RuntimeError("VK_GROUP_ID is empty")
    return -abs(int(group_id))


def vk_api_call(method: str, params: dict[str, Any], *, access_token: str | None = None) -> dict[str, Any]:
    token = access_token if access_token is not None else VK_USER_ACCESS_TOKEN
    if not token:
        raise RuntimeError("VK access token is empty")

    payload_params = {
        **params,
        "access_token": token,
        "v": VK_API_VERSION,
    }
    data = urlencode(payload_params).encode("utf-8")
    request = Request(f"https://api.vk.com/method/{method}", data=data, method="POST")
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if "error" in payload:
        raise RuntimeError(format_vk_error(payload["error"]))
    return payload.get("response", {})


def encode_multipart_formdata(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = f"----void-vk-{random.randrange(10**12, 10**13)}"
    body: list[bytes] = []

    for name, value in fields.items():
        body.append(f"--{boundary}\r\n".encode("utf-8"))
        body.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.append(str(value).encode("utf-8"))
        body.append(b"\r\n")

    for name, (filename, content, content_type) in files.items():
        body.append(f"--{boundary}\r\n".encode("utf-8"))
        body.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8")
        )
        body.append(content)
        body.append(b"\r\n")

    body.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(body), f"multipart/form-data; boundary={boundary}"


def upload_vk_wall_photo(image: bytes, filename: str = "void-wall.png") -> str:
    token = VK_PHOTO_ACCESS_TOKEN or VK_USER_ACCESS_TOKEN
    group_id = str(abs(int(VK_GROUP_ID)))
    upload = vk_api_call("photos.getWallUploadServer", {"group_id": group_id}, access_token=token)
    upload_url = upload.get("upload_url")
    if not upload_url:
        raise RuntimeError("VK photo upload_url is empty")

    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    data, request_content_type = encode_multipart_formdata(
        {},
        {"photo": (filename, image, content_type)},
    )
    request = Request(
        upload_url,
        data=data,
        method="POST",
        headers={"Content-Type": request_content_type},
    )
    with urlopen(request, timeout=60) as response:
        uploaded = json.loads(response.read().decode("utf-8"))

    saved = vk_api_call(
        "photos.saveWallPhoto",
        {
            "group_id": group_id,
            "photo": uploaded.get("photo", ""),
            "server": uploaded.get("server", ""),
            "hash": uploaded.get("hash", ""),
        },
        access_token=token,
    )
    if not saved:
        raise RuntimeError("VK photos.saveWallPhoto returned empty response")

    photo = saved[0]
    return f"photo{photo['owner_id']}_{photo['id']}"


def build_vk_image_attachment_sync(draft: dict | sqlite3.Row) -> str:
    images = generate_post_images_sync(draft)
    if images:
        return upload_vk_wall_photo(images[0], filename=f"void-{draft['id']}-vk.png")

    source_image_url = find_source_image_url(draft["source_url"] or "")
    if not source_image_url:
        raise RuntimeError("no generated image and no source image fallback")

    request = Request(source_image_url, headers={"User-Agent": "VOIDBot/1.0"})
    with urlopen(request, timeout=30) as response:
        image = response.read(8_000_000)
        content_type = response.headers.get_content_type()
    extension = ".jpg" if content_type == "image/jpeg" else ".png"
    return upload_vk_wall_photo(image, filename=f"void-{draft['id']}-source{extension}")


def load_vk_music_tracks() -> list[dict[str, Any]]:
    path = Path(VK_MUSIC_TRACKS_FILE)
    if not path.exists():
        return []

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"vk music tracks load error: {type(e).__name__}: {e}", flush=True)
        return []

    tracks = data.get("tracks", data) if isinstance(data, dict) else data
    if not isinstance(tracks, list):
        return []
    return [track for track in tracks if isinstance(track, dict) and track.get("title")]


def vk_music_track_key(track: dict[str, Any]) -> str:
    artist = str(track.get("artist") or "").strip().casefold()
    title = str(track.get("title") or "").strip().casefold()
    return f"{artist}|{title}"


def recent_vk_music_track_keys(limit: int = 8) -> list[str]:
    conn = db()
    rows = conn.execute(
        """
        SELECT music_track
        FROM vk_posts
        WHERE music_track IS NOT NULL AND music_track NOT IN ('', '{}')
        ORDER BY published_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    keys: list[str] = []
    for row in rows:
        try:
            track = json.loads(row["music_track"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(track, dict) and track.get("title"):
            keys.append(vk_music_track_key(track))
    return keys


VK_VIBE_KEYWORDS = {
    "dark": ("dark", "pain", "fallen", "phantom", "shadow", "void", "cold", "black", "fear", "alone", "пуст", "тень", "боль"),
    "future": ("future", "orbital", "space", "mercury", "digital", "machine", "signal", "neon", "tomorrow", "zavtra", "будущ", "робот", "ai"),
    "energy": ("move", "life", "energy", "fire", "power", "run", "dance", "more", "beat", "drive", "ритм", "движ", "скорост"),
    "calm": ("silent", "quiet", "ambient", "slow", "still", "peace", "soft", "сон", "тиш", "спокой"),
    "melancholy": ("pain", "waste", "gone", "lost", "without", "goodbye", "rain", "memory", "sad", "боль", "дожд", "памят", "потер"),
    "warm": ("love", "soul", "you and me", "together", "home", "heart", "summer", "light", "люб", "дом", "свет"),
    "tension": ("thrill", "danger", "warning", "pressure", "storm", "attack", "thriller", "контрол", "угроз", "тревог"),
    "night": ("night", "midnight", "moon", "dream", "noir", "ноч", "лун", "сон"),
}

VK_MODE_VIBES = {
    "frequency": {"energy", "tension", "night"},
    "midnight": {"night", "dark", "calm", "melancholy"},
    "vault": {"dark", "melancholy", "calm"},
    "future": {"future", "energy", "tension"},
    "news": {"future", "tension", "energy"},
    "signal": {"tension", "future", "energy"},
    "observation": {"calm", "melancholy", "warm"},
    "material": {"dark", "calm", "melancholy"},
}


def infer_vk_vibes(text: str) -> set[str]:
    normalized = (text or "").casefold()
    return {
        vibe
        for vibe, keywords in VK_VIBE_KEYWORDS.items()
        if any(
            re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", normalized)
            if len(keyword) <= 2
            else keyword in normalized
            for keyword in keywords
        )
    }


def post_vk_vibes(draft: dict | sqlite3.Row) -> set[str]:
    mode = str(draft["mode"] or "").casefold()
    text = " ".join(
        (
            mode,
            str(draft["title"] or ""),
            str(draft["frequency"] or ""),
            str(draft["post"] or ""),
        )
    )
    return set(VK_MODE_VIBES.get(mode, set())) | infer_vk_vibes(text)


def track_vk_vibes(track: dict[str, Any]) -> set[str]:
    explicit = {
        str(tag).strip().casefold()
        for tag in track.get("tags", [])
        if str(tag).strip().casefold() in VK_VIBE_KEYWORDS
    }
    identity = f"{track.get('artist', '')} {track.get('title', '')}"
    return explicit | infer_vk_vibes(identity)


def vk_music_track_query_key(track: dict[str, Any]) -> str:
    query = f"{track.get('artist', '')} {track.get('title', '')}"
    return " ".join(re.findall(r"[0-9a-zа-яё]+", query.casefold()))


def choose_vk_music_track(
    draft: dict | sqlite3.Row,
    excluded_track_keys: set[str] | None = None,
) -> dict[str, Any] | None:
    tracks = load_vk_music_tracks()
    if not tracks:
        return None

    post_vibes = post_vk_vibes(draft)

    def score(track: dict[str, Any]) -> int:
        track_vibes = track_vk_vibes(track)
        overlap = post_vibes & track_vibes
        return sum(3 if vibe in {"future", "dark", "energy", "calm"} else 2 for vibe in overlap)

    recent = set(recent_vk_music_track_keys(limit=8))
    shared_recent = excluded_track_keys or set()
    candidates = [
        track
        for track in tracks
        if vk_music_track_key(track) not in recent
        and vk_music_track_query_key(track) not in shared_recent
    ]
    if not candidates:
        return None
    best_score = max(score(track) for track in candidates)
    if best_score <= 0:
        return None
    best = [track for track in candidates if score(track) == best_score]

    draft_seed = str(draft["id"] if "id" in draft.keys() else sorted(post_vibes))
    return random.Random(draft_seed).choice(best)


def parse_vk_music_import(text: str) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip().strip("-* ")
        if not line or line.startswith("/"):
            continue

        parts = [part.strip() for part in line.split("|")]
        main = parts[0]
        url = parts[1] if len(parts) >= 2 and parts[1].startswith("http") else ""
        tags_part = parts[2] if len(parts) >= 3 else ""

        if " - " in main:
            artist, title = [part.strip() for part in main.split(" - ", 1)]
        elif " — " in main:
            artist, title = [part.strip() for part in main.split(" — ", 1)]
        else:
            artist, title = "", main

        if not title:
            continue

        tags = [tag.strip().lower() for tag in re.split(r"[,;]", tags_part) if tag.strip()]
        if not tags:
            tags = ["music", "culture", "night"]

        tracks.append(
            {
                "artist": artist,
                "title": title,
                "url": url,
                "tags": tags,
            }
        )
    return tracks


def import_vk_music_tracks(text: str) -> tuple[int, int]:
    imported = parse_vk_music_import(text)
    if not imported:
        return 0, len(load_vk_music_tracks())

    path = Path(VK_MUSIC_TRACKS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_vk_music_tracks()
    by_key: dict[tuple[str, str], dict[str, Any]] = {}

    for track in existing + imported:
        key = (
            str(track.get("artist", "")).strip().lower(),
            str(track.get("title", "")).strip().lower(),
        )
        by_key[key] = track

    merged = sorted(by_key.values(), key=lambda item: (str(item.get("artist", "")), str(item.get("title", ""))))
    path.write_text(json.dumps({"tracks": merged}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(imported), len(merged)


def format_vk_music_track(track: dict[str, Any] | None) -> str:
    if not track:
        return ""

    artist = str(track.get("artist", "")).strip()
    title = str(track.get("title", "")).strip()
    url = str(track.get("url", "")).strip()
    label = f"{artist} - {title}" if artist else title
    if not url and label:
        url = f"https://vk.com/audio?q={quote(label)}"
    if url:
        return f"\n\nSoundtrack: {label}\n{url}"
    return f"\n\nSoundtrack: {label}"


def post_to_vk_wall(text: str, *, force: bool = False, attachments: list[str] | None = None) -> dict[str, Any]:
    if not text.strip():
        raise RuntimeError("VK post text is empty")

    owner_id = vk_owner_id_from_group_id(VK_GROUP_ID)
    params = {
        "owner_id": str(owner_id),
        "from_group": "1",
        "message": text.strip(),
        "access_token": VK_USER_ACCESS_TOKEN,
        "v": VK_API_VERSION,
    }
    if attachments:
        params["attachments"] = ",".join(attachments)

    if VK_DRY_RUN and not force:
        safe = {**params, "access_token": "***"}
        return {"ok": True, "dry_run": True, "post_id": None, "response": {"dry_run": safe}}

    if not VK_USER_ACCESS_TOKEN:
        raise RuntimeError("VK_USER_ACCESS_TOKEN is empty")

    data = urlencode(params).encode("utf-8")
    request = Request("https://api.vk.com/method/wall.post", data=data, method="POST")
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if "error" in payload:
        raise RuntimeError(format_vk_error(payload["error"]))

    return {
        "ok": True,
        "dry_run": False,
        "post_id": payload.get("response", {}).get("post_id"),
        "response": payload,
    }


def format_vk_error(error: dict[str, Any]) -> str:
    code = error.get("error_code")
    subcode = error.get("error_subcode")
    message = error.get("error_msg") or "unknown VK API error"

    if code == 15 and subcode == 1133:
        return (
            "VK access denied: token has no permission for wall.post. "
            "Reissue VK_USER_ACCESS_TOKEN with the wall scope and make sure the user is an admin "
            "of the VK community. Keep VK_DRY_RUN=true until /vk_test works."
        )

    if code == 27:
        return (
            "VK auth type rejected this method. For wall photo uploads, set VK_PHOTO_ACCESS_TOKEN "
            "to a user access token with photos and wall permissions; group/community tokens can post text "
            "but may fail on photos.getWallUploadServer."
        )

    return f"VK API error {code}: {message}"


async def prepare_telegram_post_package(draft: dict | sqlite3.Row) -> TelegramPostPackage:
    """Prepare the complete Telegram payload before the first Bot API call."""
    text = str(draft["post"] or "")
    if not text.strip():
        raise ValueError("Telegram post text is empty")
    editorial_required = bool(
        "editorial_brief_json" in draft.keys()
        and str(draft["editorial_brief_json"] or "").strip()
    )

    image_issue: str | None = None
    try:
        generated = await asyncio.to_thread(generate_post_images_sync, draft)
        images = tuple(bytes(image) for image in generated if isinstance(image, (bytes, bytearray)) and image)
        if len(images) != len(generated):
            image_issue = "image generation returned an invalid image payload"
            images = ()
    except Exception as e:
        images = ()
        image_issue = f"image generation failed: {type(e).__name__}: {e}"

    if images:
        return TelegramPostPackage(text=text, draft_id=int(draft["id"]), images=images)

    if editorial_required:
        raise RuntimeError("required relevant editorial image is unavailable")

    source_image_url = await asyncio.to_thread(find_source_image_url, draft["source_url"] or "")
    if source_image_url:
        return TelegramPostPackage(
            text=text,
            draft_id=int(draft["id"]),
            source_image_url=source_image_url,
        )

    no_image_reason = image_issue or "image generation returned no images and source fallback is unavailable"
    return TelegramPostPackage(
        text=text,
        draft_id=int(draft["id"]),
        no_image_reason=no_image_reason,
    )


async def send_telegram_post(bot: Bot, package: TelegramPostPackage) -> TelegramPublishOutcome:
    """Send one complete channel post in the invariant media-then-text order."""
    image_count = len(package.images) or (1 if package.source_image_url else 0)
    try:
        if package.images:
            if len(package.images) == 1:
                await bot.send_photo(
                    chat_id=CHANNEL_ID,
                    photo=BufferedInputFile(
                        package.images[0], filename=f"void-{package.draft_id}-1.png"
                    ),
                )
            else:
                media = [
                    InputMediaPhoto(
                        media=BufferedInputFile(
                            image, filename=f"void-{package.draft_id}-{index}.png"
                        )
                    )
                    for index, image in enumerate(package.images, start=1)
                ]
                await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
        elif package.source_image_url:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=package.source_image_url)
        else:
            print(
                f"telegram text-only draft #{package.draft_id}: {package.no_image_reason}",
                flush=True,
            )
    except Exception as e:
        error = f"media send failed: {type(e).__name__}: {e}"
        print(f"telegram publish failed draft #{package.draft_id}: {error}", flush=True)
        return TelegramPublishOutcome(success=False, error=error)

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=package.text,
            reply_markup=catch_keyboard(package.draft_id),
            disable_web_page_preview=True,
        )
    except Exception as e:
        error = f"text send failed: {type(e).__name__}: {e}"
        print(f"telegram publish failed draft #{package.draft_id}: {error}", flush=True)
        return TelegramPublishOutcome(success=False, image_count=image_count, error=error)

    return TelegramPublishOutcome(success=True, image_count=image_count)


async def publish_draft(
    bot: Bot,
    draft_id: int,
    *,
    content_plan: dict[str, str] | None = None,
    content_topic: str = "",
    apply_planned_character_event: bool = False,
    setting_updates: dict[str, str] | None = None,
) -> str:
    if not CHANNEL_ID:
        return "CHANNEL_ID не задан. Добавь канал в Secrets."

    draft = get_draft(draft_id)
    if not draft:
        return "Черновик не найден."

    ok, reason = quality_check(draft["post"])
    if not ok:
        return f"Не публикую: {reason}. Сначала /preview {draft_id}."

    try:
        package = await prepare_telegram_post_package(draft)
    except Exception as e:
        if "editorial_brief_json" in draft.keys() and str(draft["editorial_brief_json"] or "").strip():
            delete_unpublished_draft(draft_id)
        return f"Публикация не выполнена: #{draft_id}. Подготовка пакета: {type(e).__name__}: {e}"

    outcome = await send_telegram_post(bot, package)
    if not outcome.success:
        if "editorial_brief_json" in draft.keys() and str(draft["editorial_brief_json"] or "").strip():
            delete_unpublished_draft(draft_id)
        return f"Публикация не выполнена: #{draft_id}. {outcome.error}"

    mark_published(draft_id, draft["source_url"] or "")
    if apply_planned_character_event:
        await asyncio.to_thread(apply_character_event, character_event_for_mode(str(draft["mode"] or "")))
    if content_plan is not None:
        await asyncio.to_thread(
            record_content_signature,
            content_plan,
            content_topic,
            draft_id,
        )
    for key, value in (setting_updates or {}).items():
        await asyncio.to_thread(set_setting, key, value)
    await asyncio.to_thread(apply_character_event, "publish")
    if outcome.image_count:
        return f"Опубликовано: #{draft_id}. Картинок: {outcome.image_count}"
    return f"Опубликовано: #{draft_id}. Картинок: 0"


async def publish_draft_to_vk(draft_id: int, *, force: bool = False) -> str:
    draft = get_draft(draft_id)
    if not draft:
        return "Draft not found."

    existing = get_vk_post_for_draft(draft_id)
    if existing:
        return f"VK duplicate blocked: draft #{draft_id} already published as post_id={existing['post_id']}."

    ok, reason = quality_check(draft["post"])
    if not ok:
        return f"VK publish blocked: {reason}. Check /preview {draft_id} first."

    track = await asyncio.to_thread(choose_vk_music_track, draft)
    if not track:
        return "VK publish blocked: no suitable fresh music track is available."
    post_text = f"{draft['post']}{format_vk_music_track(track)}"
    attachments: list[str] = []
    image_error = ""

    if force:
        try:
            image_attachment = await asyncio.to_thread(build_vk_image_attachment_sync, draft)
            attachments.append(image_attachment)
        except Exception as e:
            image_error = f" image=failed:{type(e).__name__}:{e}"

    try:
        result = await asyncio.to_thread(post_to_vk_wall, post_text, force=force, attachments=attachments)
    except Exception as e:
        return f"VK publish failed: {type(e).__name__}: {e}"

    status = "dry-run" if result.get("dry_run") else "published"
    post_id = result.get("post_id")
    if result.get("dry_run"):
        track_note = " track=yes" if track else " track=none"
        return f"VK {status}: draft #{draft_id}. post_id={post_id}.{track_note} image=skipped. To publish for real: /publish_vk --yes {draft_id}"

    mark_vk_published(
        draft_id,
        int(post_id or 0),
        vk_owner_id_from_group_id(VK_GROUP_ID),
        attachments=attachments,
        music_track=track,
    )
    image_note = " image=yes" if attachments else image_error or " image=none"
    track_note = " track=yes" if track else " track=none"
    return f"VK {status}: draft #{draft_id}. post_id={post_id}.{image_note}{track_note}"


async def make_news_drafts(limit: int = 5) -> tuple[int, int]:
    items = await asyncio.to_thread(fetch_news)
    saved_candidates = 0
    made_drafts = 0

    for item in items[:limit]:
        candidate_id = insert_candidate(item)
        if candidate_id:
            saved_candidates += 1

        content = (
            f"Заголовок: {item['title']}\n"
            f"Описание: {item.get('summary', '')}\n"
            f"Источник: {item.get('source_name', '')}\n"
            f"Ссылка: {item.get('url', '')}"
        )
        await generate_and_save(
            item.get("mode", "news"),
            content,
            item.get("frequency", "HUMAN"),
            item.get("source_name", ""),
            item.get("url", ""),
        )
        made_drafts += 1

    return saved_candidates, made_drafts


async def autopost_once(bot: Bot) -> str:
    items = await asyncio.to_thread(fetch_news)
    if not items:
        return "Автопостинг: новых сигналов не найдено."

    for item in items[:10]:
        state, editorial_plan, character_directive = await asyncio.to_thread(
            build_character_directive,
            item.get("title", "news"),
            "telegram",
            item.get("mode", "news"),
            False,
        )
        attitude = duo_relationship.news_attitude(
            "void", item.get("title", ""), item.get("summary", ""),
            tension=state.tension, curiosity=state.curiosity,
        )
        content = (
            f"Заголовок: {item['title']}\n"
            f"Описание: {item.get('summary', '')}\n"
            f"Источник: {item.get('source_name', '')}\n"
            f"Ссылка: {item.get('url', '')}\n"
            f"Позиция VOID: {attitude['stance']} — {attitude['tone']}\n\n"
            f"{character_directive}"
        )
        draft_id = await generate_and_save(
            item.get("mode", "news"),
            content,
            item.get("frequency", "HUMAN"),
            item.get("source_name", ""),
            item.get("url", ""),
        )
        draft = get_draft(draft_id)
        ok, reason = quality_check(draft["post"] if draft else "")
        if ok:
            result = await publish_draft(
                bot,
                draft_id,
                content_plan=editorial_plan,
                content_topic=item.get("title", "news"),
                apply_planned_character_event=True,
            )
            return f"Автопостинг: {result}"
        else:
            continue

    return "Автопостинг: сигналы были, но quality gate всё зарезал. Редкий случай, когда цензура оказалась полезной."


def next_content_plan_slot(*, advance: bool = True) -> tuple[int, dict[str, str]]:
    current = int(get_setting("auto_content_index", "0") or "0")
    slot = CONTENT_PLAN[current % len(CONTENT_PLAN)]
    if advance:
        set_setting("auto_content_index", str(current + 1))
    return current, slot


async def autopost_void_signal_once(bot: Bot) -> str:
    index, slot = await asyncio.to_thread(next_content_plan_slot, advance=False)
    mode = slot["mode"]
    frequency = slot["frequency"]
    _, editorial_plan, character_directive = await asyncio.to_thread(
        build_character_directive,
        str(slot.get("brief", slot["name"])),
        "telegram",
        mode,
        False,
    )
    content = (
        f"CONTENT_PLAN_INDEX: {index}\n"
        f"RUBRIC: {slot['name']}\n"
        f"PLATFORM: Telegram\n"
        f"BRIEF:\n{slot['brief']}\n\n"
        f"{character_directive}\n\n"
        "Make an original VOID post. Do not mention that this came from a plan. "
        "Do not imitate news. No external source is required."
    )

    draft_id = await generate_and_save(
        mode,
        content,
        frequency,
        "VOID",
        f"manual://auto/{mode}/{now_iso()}",
    )
    draft = get_draft(draft_id)
    ok, reason = quality_check(draft["post"] if draft else "")
    if not ok:
        return f"VOID-план: черновик #{draft_id} не опубликован: {reason}"

    result = await publish_draft(
        bot,
        draft_id,
        content_plan=editorial_plan,
        content_topic=str(slot.get("brief", slot["name"])),
        apply_planned_character_event=True,
        setting_updates={"auto_content_index": str(index + 1)},
    )
    return f"VOID-план: {result}"


def eligible_rubric_slots(now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(MOSCOW_TZ)
    hour = current.hour
    eligible = [slot for slot in RUBRIC_SCHEDULE if hour in slot.get("hours", [])]
    if eligible:
        return eligible
    return [slot for slot in RUBRIC_SCHEDULE if slot.get("voice") in {"void", "news"}]


def eligible_schedule_slots(schedule: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    current = now or datetime.now(MOSCOW_TZ)
    hour = current.hour
    eligible = [slot for slot in schedule if hour in slot.get("hours", [])]
    return eligible or schedule


def schedule_recent_value(recent_key: str, slot_name: str) -> str:
    recent = [item for item in get_setting(recent_key, "").split(",") if item]
    recent.append(slot_name)
    return ",".join(recent[-8:])


def registered_void_rubrics() -> set[str]:
    return {
        str(item.get("name") or "")
        for item in (*RUBRIC_SCHEDULE, *TELEGRAM_VOID_SCHEDULE)
        if str(item.get("name") or "")
    } | {"MATERIAL / МАТЕРИЯ", "Culture", "approved_backstage", "canonical_story"}


def build_scheduled_content_brief(
    *,
    slot: dict[str, Any],
    editorial_plan: dict[str, str],
    source_reference: str,
    platform: str,
    source_type: str,
) -> editorial_policy.ContentBrief:
    rubric = str(slot.get("name") or "")
    thesis = str(editorial_plan.get("meaning_thought") or slot.get("brief") or "").strip()
    visual_subject = str(
        editorial_plan.get("scene_instruction")
        or editorial_plan.get("media")
        or slot.get("brief")
        or ""
    ).strip()
    content_brief = editorial_policy.build_brief(
        destination=platform,
        scheduled_slot=f"{platform}:{slot.get('mode', 'signal')}",
        source_type=source_type,
        source_reference=source_reference,
        rubric=rubric,
        thesis=thesis,
        context_reason=(
            "A sourced current event selected by the configured rubric."
            if source_type == "current_event_with_source"
            else "The configured publication schedule selected this rubric and approved semantic axis."
        ),
        visual_subject=visual_subject,
        visual_relation=(
            "The selected concrete scene must reveal the fixed thesis through VOID's visible-hidden boundary."
        ),
        allowed_rubrics=registered_void_rubrics(),
        required_elements=(visual_subject,),
        music_required=platform == "vk",
    )
    print(
        "EDITORIAL_BRIEF metadata="
        + json.dumps(
            {
                "post_id": content_brief.post_id,
                "persona": content_brief.persona,
                "scheduled_slot": content_brief.scheduled_slot,
                "rubric": content_brief.rubric,
                "source_type": content_brief.source_type,
                "editorial_contract_version": content_brief.editorial_contract_version,
                "persona_policy_version": content_brief.persona_policy_version,
                "visual_code_version": content_brief.visual_code_version,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        flush=True,
    )
    return content_brief


SCHEDULED_GENERATION_ATTEMPTS = 2
SEMANTIC_HISTORY_LIMIT = 8


def scheduled_reason_code(reason: str) -> str:
    safe_codes = editorial_policy.REASON_CODES | {
        "near_duplicate_semantics",
        "repeated_digital_attention_thesis",
    }
    return reason if reason in safe_codes else "text_quality_rejected"


def _cycle_after(
    values: list[str],
    previous: str,
) -> list[str]:
    if not values:
        return []
    if previous not in values:
        return list(values)
    start = values.index(previous) + 1
    return values[start:] + values[:start]


def _last_published_axis(
    recent_signatures: list[dict[str, str]],
    key: str,
    allowed: set[str],
) -> str:
    return next(
        (
            str(item.get(key, ""))
            for item in reversed(recent_signatures)
            if str(item.get(key, "")) in allowed
        ),
        "",
    )


def semantic_theme_candidates(
    mode: str,
    recent_signatures: list[dict[str, str]] | None = None,
) -> list[str]:
    allowed = set(
        MODE_SEMANTIC_THEMES.get(
            mode,
            ("craft", "city", "work", "relationship", "play", "maintenance", "body", "absurdity"),
        )
    )
    history = list(recent_signatures or [])
    previous = _last_published_axis(
        history,
        "semantic_theme",
        set(SEMANTIC_THEME_ORDER),
    )
    rotated = _cycle_after(list(SEMANTIC_THEME_ORDER), previous)
    return [theme for theme in rotated if theme in allowed]


def choose_semantic_theme(
    mode: str,
    recent_signatures: list[dict[str, str]] | None = None,
) -> str:
    candidates = semantic_theme_candidates(mode, recent_signatures)
    if not candidates:
        raise ValueError(f"no semantic themes configured for mode: {mode}")
    return candidates[0]


def select_editorial_axes(
    semantic_theme: str,
    recent_signatures: list[dict[str, str]] | None = None,
) -> dict[str, str]:
    history = list(recent_signatures or [])
    cards = list(MEANING_CARDS.get(semantic_theme, ()))
    if not cards:
        raise ValueError(f"no meaning cards configured for theme: {semantic_theme}")
    card_keys = [str(card["key"]) for card in cards]
    previous_card = _last_published_axis(
        history,
        "meaning_key",
        set(card_keys),
    )
    card_by_key = {str(card["key"]): card for card in cards}
    card = card_by_key[_cycle_after(card_keys, previous_card)[0]]

    narrative_keys = [str(item["key"]) for item in NARRATIVE_SHAPES]
    previous_narrative = _last_published_axis(
        history,
        "narrative_shape",
        set(narrative_keys),
    )
    narrative_key = _cycle_after(narrative_keys, previous_narrative)[0]
    narrative = next(
        item for item in NARRATIVE_SHAPES if item["key"] == narrative_key
    )

    scene_keys = [str(item["key"]) for item in SCENE_AXES]
    previous_scene = _last_published_axis(
        history,
        "scene_axis",
        set(scene_keys),
    )
    scene_key = _cycle_after(scene_keys, previous_scene)[0]
    scene = next(item for item in SCENE_AXES if item["key"] == scene_key)

    return {
        "meaning_key": str(card["key"]),
        "meaning_thought": str(card["thought"]),
        "moral_axis": str(card["moral"]),
        "narrative_shape": narrative_key,
        "narrative_instruction": str(narrative["instruction"]),
        "scene_axis": scene_key,
        "scene_instruction": str(scene["instruction"]),
    }


def recent_scheduled_posts(platform: str, limit: int = SEMANTIC_HISTORY_LIMIT) -> list[str]:
    prefix = "manual://vk/schedule/%" if platform == "vk" else "manual://telegram/void/%"
    conn = db()
    rows = conn.execute(
        """
        SELECT post FROM drafts
        WHERE source_url LIKE ?
          AND published_at IS NOT NULL
        ORDER BY id DESC
        LIMIT ?
        """,
        (prefix, max(1, limit)),
    ).fetchall()
    conn.close()
    return [str(row["post"] or "") for row in reversed(rows)]


_SEMANTIC_STOPWORDS = {
    "void", "comment", "signal", "observation", "future", "file", "midnight",
    "vault", "human", "attention", "источник", "который", "которая", "которые",
    "этого", "потому", "просто", "сейчас", "только", "через", "между", "когда",
    "чтобы", "после", "перед", "можно", "нужно", "очень", "один", "одна", "свою",
    "себя", "этот", "такая", "такой", "тоже", "если", "есть", "будет", "быть",
}


def _semantic_tokens(post: str) -> set[str]:
    body = re.split(r"\n\s*Источник\s*:", post, maxsplit=1, flags=re.I)[0]
    return {
        token
        for token in re.findall(r"[a-zа-яё]{4,}", body.casefold())
        if token not in _SEMANTIC_STOPWORDS
    }


def repeats_default_digital_thesis(post: str) -> bool:
    value = post.casefold()
    digital_context = (
        "цифров", "систем", "экран", "лент", "уведомлен", "алгоритм",
        "платформ", "интерфейс", "онлайн", "шум",
    )
    attention_context = (
        "вниман", "фокус", "замеча", "отвлека", "рассеив", "пауза",
        "привычк", "осознан",
    )
    preservation_context = (
        "человек", "человечес", "себя", "жив", "свобод", "выбор", "сохран",
    )
    return (
        any(stem in value for stem in digital_context)
        and any(stem in value for stem in attention_context)
        and any(stem in value for stem in preservation_context)
    )


def semantic_repetition_reason(
    post: str,
    recent_posts: list[str],
) -> str:
    if repeats_default_digital_thesis(post):
        recent_default_theses = sum(
            repeats_default_digital_thesis(previous) for previous in recent_posts
        )
        if recent_default_theses >= 2:
            return "repeated_digital_attention_thesis"

    candidate_tokens = _semantic_tokens(post)
    for previous in recent_posts:
        previous_tokens = _semantic_tokens(previous)
        shared = candidate_tokens & previous_tokens
        union = candidate_tokens | previous_tokens
        if len(shared) >= 12 and union and len(shared) / len(union) >= 0.38:
            return "near_duplicate_semantics"
    return ""


def semantic_gate_decision(
    post: str,
    recent_posts: list[str],
    summary: SemanticSummary,
) -> SemanticGateDecision:
    reason = semantic_repetition_reason(post, recent_posts)
    return SemanticGateDecision(
        accepted=not reason,
        reason=reason,
        central_thesis=summary.central_thesis,
        conclusion=summary.conclusion,
        narrative_shape=summary.narrative_shape,
        key_meanings=summary.key_meanings,
    )


def build_semantic_retry_content(
    original_content: str,
    semantic_theme: str,
    decision: SemanticGateDecision,
) -> str:
    key_meanings = "; ".join(decision.key_meanings)
    return (
        f"{original_content}\n\n"
        "BOUNDED RETRY: the previous candidate was blocked before saving. "
        f"REASON: {decision.reason}\n\n"
        "SAFE SEMANTIC SUMMARY OF THE REJECTED CANDIDATE:\n"
        f"CENTRAL_THESIS: {decision.central_thesis}\n"
        f"CONCLUSION: {decision.conclusion}\n"
        f"NARRATIVE_SHAPE: {decision.narrative_shape}\n"
        f"KEY_MEANINGS: {key_meanings}\n\n"
        "Hard constraints:\n"
        "- Do not repeat or paraphrase that central thesis.\n"
        "- Do not repeat that conclusion or moral.\n"
        "- Do not reuse that narrative shape.\n"
        "- Do not rebuild the post from the same set of key meanings.\n"
        f"- Stay within the selected semantic theme '{semantic_theme}', but use "
        "a different concrete scene and reach a substantially different conclusion."
    )


def _published_schedule_names(
    schedule: list[dict[str, Any]],
    platform: str,
) -> list[str]:
    name_by_mode = {
        str(slot.get("mode", "")): str(slot["name"])
        for slot in schedule
    }
    return [
        name_by_mode[mode]
        for item in get_recent_content_signatures(16)
        if str(item.get("platform", "")) == platform
        if (mode := str(item.get("mode", ""))) in name_by_mode
    ]


def choose_schedule_slot(
    schedule: list[dict[str, Any]],
    recent_names: list[str],
    now: datetime | None = None,
) -> dict[str, Any]:
    slots = eligible_schedule_slots(schedule, now)
    filtered = [
        slot for slot in slots
        if str(slot["name"]) not in recent_names[-3:]
    ]
    if filtered:
        slots = filtered
    return slots[0]


def choose_scheduled_rubric(now: datetime | None = None) -> dict[str, Any]:
    return choose_schedule_slot(
        RUBRIC_SCHEDULE,
        _published_schedule_names(RUBRIC_SCHEDULE, "vk"),
        now,
    )


def choose_telegram_schedule_slot(now: datetime | None = None) -> dict[str, Any]:
    return choose_schedule_slot(
        TELEGRAM_VOID_SCHEDULE,
        _published_schedule_names(TELEGRAM_VOID_SCHEDULE, "telegram"),
        now,
    )


def rubric_schedule_text(now: datetime | None = None) -> str:
    current = now or datetime.now(MOSCOW_TZ)
    lines = [
        "VK/VOID rubric schedule",
        "",
        f"Moscow now: {current:%H:%M}",
        "",
        "Fixed windows:",
        "00-02: MIDNIGHT / VOID",
        "19-22: FREQUENCY / VOID",
        "22-23: THE VAULT / VOID",
        "",
        "Random pools:",
        "09-18: SIGNAL, OBSERVATION, FUTURE FILE, NEWS",
        "18-21: evening VOID formats and NEWS",
        "",
        "Eligible now:",
    ]
    for slot in eligible_rubric_slots(current):
        lines.append(f"- {slot['name']} ({slot['voice']}, weight={slot.get('weight', 1)})")
    return "\n".join(lines)


def telegram_schedule_text(now: datetime | None = None) -> str:
    current = now or datetime.now(MOSCOW_TZ)
    lines = [
        "Telegram rubric schedule",
        "",
        f"Moscow now: {current:%H:%M}",
        "",
        "VOID:",
        "00-02: MIDNIGHT",
        "19-22: FREQUENCY",
        "22-23: THE VAULT",
        "09-18: SIGNAL / OBSERVATION / FUTURE FILE / NEWS",
        "",
        "Eligible VOID now:",
    ]
    for slot in eligible_schedule_slots(TELEGRAM_VOID_SCHEDULE, current):
        lines.append(f"- {slot['name']} (weight={slot.get('weight', 1)})")
    return "\n".join(lines)


async def save_scheduled_rubric_draft(slot: dict[str, Any]) -> int:
    voice = str(slot.get("voice", "void"))
    name = str(slot.get("name", "Scheduled Signal"))
    brief = str(slot.get("brief", "Make an original post for the shared public."))
    mode = str(slot.get("mode", "signal"))
    recent_signatures = await asyncio.to_thread(get_recent_content_signatures)
    semantic_theme = (
        ""
        if voice == "news"
        else choose_semantic_theme(mode, recent_signatures)
    )
    _, editorial_plan, character_directive = await asyncio.to_thread(
        build_character_directive,
        brief,
        "vk",
        mode,
        False,
        semantic_theme,
    )

    content = (
        f"RUBRIC: {name}\n"
        f"VOICE: {voice}\n"
        f"PLATFORM: VK shared public\n"
        f"BRIEF:\n{brief}\n\n"
        f"{character_directive}\n\n"
        "Make an original post for the shared VK public. Do not mention that this came from a schedule."
    )

    if voice == "news":
        items = await asyncio.to_thread(fetch_news)
        for item in items[:10]:
            source_reference = str(item.get("url") or "").strip()
            if not source_reference:
                continue
            content_brief = build_scheduled_content_brief(
                slot=slot,
                editorial_plan=editorial_plan,
                source_reference=source_reference,
                platform="vk",
                source_type="current_event_with_source",
            )
            news_content = (
                f"Заголовок: {item['title']}\n"
                f"Описание: {item.get('summary', '')}\n"
                f"Источник: {item.get('source_name', '')}\n"
                f"Ссылка: {item.get('url', '')}"
                f"\n\n{character_directive}"
            )
            draft_id = await generate_scheduled_draft(
                mode=item.get("mode", "news"),
                content=news_content,
                frequency=item.get("frequency", "HUMAN"),
                source_name=item.get("source_name", ""),
                source_url=source_reference,
                platform="vk",
                semantic_theme="",
                editorial_brief=content_brief,
            )
            draft = get_draft(draft_id)
            ok, _ = quality_check(draft["post"] if draft else "")
            if ok:
                await asyncio.to_thread(
                    record_content_signature,
                    editorial_plan,
                    item.get("title", brief),
                    draft_id,
                )
                return draft_id
        raise RuntimeError("fresh news signals not found")

    source_reference = f"manual://vk/schedule/{slot.get('mode', 'signal')}/{now_iso()}"
    content_brief = build_scheduled_content_brief(
        slot=slot,
        editorial_plan=editorial_plan,
        source_reference=source_reference,
        platform="vk",
        source_type="scheduled_rubric",
    )
    draft_id = await generate_scheduled_draft(
        mode=mode,
        content=content,
        frequency=str(slot.get("frequency", "HUMAN")),
        source_name="VOID / VK scheduled rubric",
        source_url=source_reference,
        platform="vk",
        semantic_theme=semantic_theme,
        editorial_brief=content_brief,
    )
    await asyncio.to_thread(
        record_content_signature,
        editorial_plan,
        f"semantic_theme:{semantic_theme}|{brief}",
        draft_id,
    )
    return draft_id


async def save_telegram_void_scheduled_draft(
    slot: dict[str, Any],
) -> tuple[int, dict[str, str], str]:
    voice = str(slot.get("voice", "void"))
    name = str(slot.get("name", "Telegram VOID"))
    brief = str(slot.get("brief", "Make an original Telegram post."))
    mode = str(slot.get("mode", "signal"))
    recent_signatures = await asyncio.to_thread(get_recent_content_signatures)
    semantic_theme = (
        ""
        if voice == "news"
        else choose_semantic_theme(mode, recent_signatures)
    )
    _, editorial_plan, character_directive = await asyncio.to_thread(
        build_character_directive,
        brief,
        "telegram",
        mode,
        False,
        semantic_theme,
    )

    if voice == "news":
        items = await asyncio.to_thread(fetch_news)
        for item in items[:10]:
            source_reference = str(item.get("url") or "").strip()
            if not source_reference:
                continue
            content_brief = build_scheduled_content_brief(
                slot=slot,
                editorial_plan=editorial_plan,
                source_reference=source_reference,
                platform="telegram",
                source_type="current_event_with_source",
            )
            content = (
                f"Заголовок: {item['title']}\n"
                f"Описание: {item.get('summary', '')}\n"
                f"Источник: {item.get('source_name', '')}\n"
                f"Ссылка: {item.get('url', '')}"
                f"\n\n{character_directive}"
            )
            draft_id = await generate_scheduled_draft(
                mode=item.get("mode", "news"),
                content=content,
                frequency=item.get("frequency", "HUMAN"),
                source_name=item.get("source_name", ""),
                source_url=source_reference,
                platform="telegram",
                semantic_theme="",
                editorial_brief=content_brief,
            )
            draft = get_draft(draft_id)
            ok, _ = quality_check(draft["post"] if draft else "")
            if ok:
                return draft_id, editorial_plan, item.get("title", brief)
        raise RuntimeError("fresh news signals not found")

    content = (
        f"RUBRIC: {name}\n"
        f"PLATFORM: Telegram VOID channel\n"
        f"BRIEF:\n{brief}\n\n"
        f"{character_directive}\n\n"
        "Make an original VOID post for Telegram. Do not mention that this came from a schedule."
    )
    source_reference = f"manual://telegram/void/{slot.get('mode', 'signal')}/{now_iso()}"
    content_brief = build_scheduled_content_brief(
        slot=slot,
        editorial_plan=editorial_plan,
        source_reference=source_reference,
        platform="telegram",
        source_type="scheduled_rubric",
    )
    draft_id = await generate_scheduled_draft(
        mode=mode,
        content=content,
        frequency=str(slot.get("frequency", "HUMAN")),
        source_name="VOID / Telegram scheduled rubric",
        source_url=source_reference,
        platform="telegram",
        semantic_theme=semantic_theme,
        editorial_brief=content_brief,
    )
    return draft_id, editorial_plan, f"semantic_theme:{semantic_theme}|{brief}"


async def publish_telegram_void_scheduled_once(bot: Bot) -> str:
    slot = await asyncio.to_thread(choose_telegram_schedule_slot)
    try:
        draft_id, editorial_plan, content_topic = await save_telegram_void_scheduled_draft(slot)
    except Exception as e:
        return f"Telegram VOID schedule failed: {slot.get('name')}: {type(e).__name__}: {e}"

    draft = get_draft(draft_id)
    ok, reason = quality_check(draft["post"] if draft else "")
    if not ok:
        return f"Telegram VOID schedule: draft #{draft_id} blocked: {reason}"
    result = await publish_draft(
        bot,
        draft_id,
        content_plan=editorial_plan,
        content_topic=content_topic,
        apply_planned_character_event=True,
        setting_updates={
            "telegram_void_recent": schedule_recent_value(
                "telegram_void_recent", str(slot["name"])
            )
        },
    )
    return f"Telegram VOID schedule: {slot['name']} -> {result}"


async def autopost_scheduled_once(bot: Bot) -> str:
    return await publish_telegram_void_scheduled_once(bot)


async def make_scheduled_rubric_draft_once() -> str:
    slot = await asyncio.to_thread(choose_scheduled_rubric)
    draft_id = await save_scheduled_rubric_draft(slot)
    return f"Scheduled VK draft: #{draft_id}\nRubric: {slot['name']} / {slot['voice']}\n/preview {draft_id}"


async def auto_loop(bot: Bot) -> None:
    while True:
        try:
            enabled = get_setting("auto_publish", "0") == "1"
            if enabled:
                slot = current_void_schedule_slot()
                last_slot = get_setting("telegram_void_last_slot", "")
                if slot and slot != last_slot:
                    # Claim first so a slow send or restart cannot duplicate a slot.
                    set_setting("telegram_void_last_slot", slot)
                    result = await publish_telegram_void_scheduled_once(bot)
                    print(result, flush=True)
        except Exception as e:
            print(f"auto_loop error: {type(e).__name__}: {e}", flush=True)

        await asyncio.sleep(20)


@router.message(CommandStart())
async def start(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("delegate_"):
        token = parts[1].removeprefix("delegate_")
        row = accept_delegation_invite(token, message.from_user.id, message.from_user.full_name)
        if not row:
            await message.answer("Ссылка недействительна, уже использована или устарела.")
            return
        delegation = delegation_from_row(row)
        intro = delegated_messaging.introduction(delegation)
        await message.answer(intro)
        save_delegated_message(int(row["id"]), "assistant", intro)
        set_delegation_status(int(row["id"]), "active")
        await message.bot.send_message(
            delegation.owner_user_id,
            f"{delegation.contact_name} открыл(а) ссылку. VOID начал поручение #{row['id']}.",
        )
        return
    if message.from_user.id != ADMIN_ID:
        remember_reachable_peer(
            message.from_user.id, message.from_user.full_name,
            (delegated_messaging.utc_now() + timedelta(hours=24)).isoformat(timespec="seconds"),
        )
        await ensure_contact_named(message)
    set_dialog_enabled(message.from_user.id, True)
    await message.answer(
        welcome_text(),
        reply_markup=reply_main_keyboard(),
    )
        
@router.message(Command("help"))
async def help_command(message: Message):
    await message.answer(
        "Command rooms:\n\n"
        "/commands - core, drafts, cross-posting\n"
        "/vk_commands - VK publisher, VK music, playlist sync\n\n"
        "You can still write ordinary text to talk with VOID.",
        reply_markup=reply_main_keyboard(),
    )


@router.message(Command("commands"))
async def commands_command(message: Message):
    await message.answer(commands_text(), reply_markup=reply_main_keyboard())


@router.message(Command("vk_commands"))
async def vk_commands_command(message: Message):
    await message.answer(vk_commands_text(), reply_markup=reply_main_keyboard())

@router.message(Command("dialog"))
async def dialog_command(message: Message):
    set_dialog_enabled(message.from_user.id, True)
    await message.answer("Диалог всегда включён. Просто напиши мне текст.", reply_markup=reply_main_keyboard())


@router.message(Command("reset"))
async def reset_command(message: Message):
    clear_dialog_context(message.from_user.id)
    await message.answer("🧹 Контекст диалога очищен. Можно начинать заново.", reply_markup=reply_main_keyboard())


@router.message(Command("status"))
async def status_command(message: Message):
    session = get_dialog_session(message.from_user.id)

    await message.answer(
        f"Диалог: всегда ON\n"
        f"Характер: {session['personality']}\n\n"
        f"{crosspost_status_text()}",
        reply_markup=reply_main_keyboard(),
    )


@router.message(Command("character"))
async def character_command(message: Message):
    state = await asyncio.to_thread(load_character_state)
    await message.answer(void_character.format_status(state), reply_markup=reply_main_keyboard())


@router.message(Command("character_event"))
async def character_event_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=1)
    allowed = sorted(void_character.EVENT_DELTAS)
    if len(parts) < 2 or parts[1].strip() not in void_character.EVENT_DELTAS:
        await message.answer("Используй: /character_event <event>\n" + ", ".join(allowed))
        return
    state = await asyncio.to_thread(apply_character_event, parts[1].strip())
    await message.answer(void_character.format_status(state))


@router.message(Command("character_set"))
async def character_set_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in void_character.AXES:
        await message.answer("Используй: /character_set <axis> <0-100>\n" + ", ".join(void_character.AXES))
        return
    try:
        value = int(parts[2])
    except ValueError:
        await message.answer("Значение должно быть числом от 0 до 100.")
        return
    state = await asyncio.to_thread(set_character_axis, parts[1], value)
    await message.answer(void_character.format_status(state))


@router.message(Command("character_simulate"))
async def character_simulate_command(message: Message):
    parts = (message.text or "").split()
    try:
        count = max(1, min(30, int(parts[1]))) if len(parts) > 1 else 10
    except ValueError:
        count = 10
    state = await asyncio.to_thread(load_character_state)
    recent = await asyncio.to_thread(get_recent_content_signatures, 16)
    plans = void_character.simulate(state, recent, count=count)
    lines = ["VOID simulation · состояние базы не изменено", ""]
    for index, plan in enumerate(plans, 1):
        lines.append(
            f"{index}. {plan['event']} → {plan['facet']} · {plan['state']}\n"
            f"   {plan['content_format_label']} / {plan['format']} / {plan['hook']}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("relationship"))
async def relationship_command(message: Message):
    state = await asyncio.to_thread(load_relationship_state)
    await message.answer(duo_relationship.format_status(state))


@router.message(Command("relationship_event"))
async def relationship_event_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or parts[1] not in duo_relationship.EVENT_DELTAS:
        await message.answer("Используй: /relationship_event <event> [topic]\n" + ", ".join(sorted(duo_relationship.EVENT_DELTAS)))
        return
    state = await asyncio.to_thread(apply_relationship_event, parts[1], topic=parts[2] if len(parts) > 2 else "")
    await message.answer(duo_relationship.format_status(state))


@router.message(Command("delegate_stop"))
async def delegate_stop_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Использование: /delegate_stop ID")
        return
    delegation_id = int(parts[1])
    row = get_delegation(delegation_id)
    if not row or row["owner_user_id"] != message.from_user.id:
        await message.answer("Поручение не найдено.")
        return
    if row.get("contact_chat_id"):
        await message.bot.send_message(int(row["contact_chat_id"]), "Разговор завершён. Спасибо.")
    purge_delegation(delegation_id, "owner_stopped")
    await message.answer("Поручение завершено; сессия и переписка удалены, имя осталось в адресной книге.")


@router.message(Command("delegate_reply"))
async def delegate_reply_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer("Использование: /delegate_reply ID текст")
        return
    delegation_id = int(parts[1])
    row = get_delegation(delegation_id)
    if not row or row["owner_user_id"] != message.from_user.id or row["status"] != "paused":
        await message.answer("Нет приостановленного поручения с таким ID.")
        return
    await message.bot.send_message(int(row["contact_chat_id"]), parts[2])
    save_delegated_message(delegation_id, "assistant", parts[2])
    set_delegation_status(delegation_id, "active")
    await message.answer("Твой ответ отправлен; VOID может продолжить в рамках поручения.")


@router.message(Command("contact_add"))
async def contact_add_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /contact_add TELEGRAM_ID Имя")
        return
    chat_id = int(parts[1])
    try:
        chat = await message.bot.get_chat(chat_id)
        display_name = " ".join(part for part in [chat.first_name or "", chat.last_name or ""] if part).strip()
        row = save_named_contact(message.from_user.id, chat_id, display_name or str(chat_id), parts[2])
    except Exception as exc:
        await message.answer(f"Не записал контакт: {exc}")
        return
    await message.answer(f"Записал: {row['alias']} → {row['display_name']} ({chat_id}).")


@router.message(Command("contact_candidates"))
async def contact_candidates_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    ids = list_previous_contact_ids(message.from_user.id)
    if not ids:
        await message.answer("Незаписанных прежних собеседников не нашёл.")
        return
    lines = ["Ранее писали боту:"]
    for chat_id in ids:
        try:
            chat = await message.bot.get_chat(chat_id)
            name = " ".join(part for part in [chat.first_name or "", chat.last_name or ""] if part).strip()
        except Exception:
            name = "имя недоступно"
        lines.append(f"• {chat_id} — {name}")
    lines.append("\nЗаписать: /contact_add TELEGRAM_ID Имя")
    await message.answer("\n".join(lines))


@router.message(Command("persona"))
async def persona_command(message: Message):
    await message.answer(
        "Доступные характеры:\n\n"
        "observer\n"
        "analyst\n"
        "philosopher\n"
        "cynic\n"
        "calm\n\n"
        "Пример:\n"
        "/persona analyst"
    )


@router.message(F.text.startswith("/persona "))
async def persona_set_command(message: Message):
    personality = message.text.split(maxsplit=1)[1].strip().lower()

    allowed = {
        "observer",
        "analyst",
        "philosopher",
        "cynic",
        "calm",
    }

    if personality not in allowed:
        await message.answer("Неизвестный характер.")
        return

    set_personality(message.from_user.id, personality)

    await message.answer(
        f"🎭 Характер VOID изменён: {personality}"
    )

@router.message(Command("news"))
@router.message(Command("scan"))
async def news_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Ищу новости. Сейчас попробую найти сигнал, а не просто очередной пресс-релиз с галстуком.")
    saved, drafts = await make_news_drafts(limit=5)
    await message.answer(f"Готово. Кандидатов: {saved}. Черновиков: {drafts}.\n\n/drafts — посмотреть")


@router.message(Command("discuss_news"))
async def discuss_news_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    await message.answer("Ищу один общий сигнал для приватного разговора Naz и VOID.")
    items = await asyncio.to_thread(fetch_news)
    if not items:
        await message.answer("Свежего сигнала не нашлось.")
        return
    item = items[0]
    state, editorial_plan, directive = await asyncio.to_thread(
        build_character_directive, item.get("title", "news"), "telegram", item.get("mode", "news")
    )
    attitude = duo_relationship.news_attitude(
        "void", item.get("title", ""), item.get("summary", ""),
        tension=state.tension, curiosity=state.curiosity,
    )
    private_material = (
        f"Новость: {item.get('title', '')}. "
        f"Факты: {item.get('summary', '')[:700]}. "
        f"Моя первичная реакция: {attitude['tone']}."
    )
    try:
        private_thought = await asyncio.to_thread(build_void_fragment_for_naz_sync, private_material)
        exchange_path = queue_void_fragment_for_naz(
            private_thought,
            source_event="shared_news_discussion",
            topic=item.get("title", ""),
        )
        apply_relationship_event("news_discussion", topic=item.get("title", ""))
    except Exception as exc:
        await message.answer(f"Не смог начать разговор: {exc}")
        return
    content = (
        f"Заголовок: {item.get('title', '')}\nОписание: {item.get('summary', '')}\n"
        f"Источник: {item.get('source_name', '')}\nСсылка: {item.get('url', '')}\n"
        f"Позиция VOID: {attitude['stance']} — {attitude['tone']}\n\n{directive}"
    )
    draft_id = await generate_and_save(
        item.get("mode", "news"), content, item.get("frequency", "HUMAN"),
        item.get("source_name", ""), item.get("url", ""),
    )
    await asyncio.to_thread(
        record_content_signature,
        editorial_plan,
        item.get("title", "news"),
        draft_id,
    )
    await message.answer(
        f"Общий сигнал: {item.get('title', '')}\n"
        f"VOID-черновик: #{draft_id}\n"
        f"Приватная мысль для Naz: {exchange_path.name if exchange_path else 'exchange disabled'}\n"
        "Naz получит не пост VOID, а отдельный импульс из разговора."
    )


@router.message(Command("candidates"))
async def candidates_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    rows = list_candidates(10)
    if not rows:
        await message.answer("Кандидатов нет. /news — просканировать.")
        return

    lines = ["Кандидаты:"]
    for r in rows:
        lines.append(f"#{r['id']} · {r['frequency']} · score {r['score']}\n{r['title']}")
    await message.answer("\n\n".join(lines))


@router.message(Command("draft"))
async def draft_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Используй: /draft ID")
        return

    candidate = get_candidate(int(parts[1]))
    if not candidate:
        await message.answer("Кандидат не найден.")
        return

    content = (
        f"Заголовок: {candidate['title']}\n"
        f"Описание: {candidate['summary'] or ''}\n"
        f"Источник: {candidate['source_name'] or ''}\n"
        f"Ссылка: {candidate['url'] or ''}"
    )
    draft_id = await generate_and_save(
        "news",
        content,
        candidate["frequency"] or "HUMAN",
        candidate["source_name"] or "",
        candidate["url"] or "",
    )
    await message.answer(f"Черновик создан: #{draft_id}\n/preview {draft_id}")


async def manual_like(message: Message, mode: str, text: str):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    text = (text or "").strip()
    if len(text) < 5:
        await message.answer("Текста мало. VOID посмотрел на это и не нашёл даже шума.")
        return

    frequency, score = score_item(text, text)
    if frequency == "NOISE":
        frequency = "HUMAN"

    await message.answer(f"Принял. Режим: {MODE_RUBRICS.get(mode, mode)}. Сейчас сделаю не душно. Попытаюсь.")
    draft_id = await generate_and_save(mode, text, frequency, "VOID manual signal", f"manual://{mode}/{now_iso()}")
    await message.answer(f"Черновик создан: #{draft_id}\n/preview {draft_id}\n/publish {draft_id}")


@router.message(Command("manual"))
async def manual_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /manual твоя мысль")
        return
    await manual_like(message, "manual", parts[1])


@router.message(Command("signal"))
async def signal_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /signal мысль или сигнал")
        return
    await manual_like(message, "signal", parts[1])


@router.message(Command("midnight"))
async def midnight_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /midnight ночная мысль")
        return
    await manual_like(message, "midnight", parts[1])


@router.message(Command("frequency"))
async def frequency_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /frequency музыка, настроение или культурная волна")
        return
    await manual_like(message, "frequency", parts[1])


@router.message(Command("observation"))
async def observation_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /observation наблюдение")
        return
    await manual_like(message, "observation", parts[1])


@router.message(Command("future"))
async def future_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /future тема будущего")
        return
    await manual_like(message, "future", parts[1])


@router.message(Command("gaming_plan"))
async def gaming_plan_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=1)
    topic = parts[1].strip() if len(parts) > 1 else "игры как человеческое пространство"
    plan = gaming_vertical.plan_gaming_content("void", topic, get_recent_content_signatures(), platform="telegram")
    await message.answer(
        f"🎮 Игровой план VOID\n\nРубрика: {plan['intent']}\nФормат: {plan['format']}\n"
        f"Коммерческий угол: {plan['commercial_angle']}\nТема: {topic}"
    )


async def gaming_draft(message: Message, *, commercial: bool = False) -> None:
    if not is_admin(message):
        await message.answer(admin_required())
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or len(parts[1].strip()) < 3:
        await message.answer("Используй: /gaming тема игры, механики или явления")
        return
    topic = parts[1].strip()
    plan = gaming_vertical.plan_gaming_content(
        "void", topic, get_recent_content_signatures(), platform="telegram", commercial=commercial
    )
    instructions = f"{VOID_CORE_PROMPT}\n\n{gaming_vertical.prompt_context('void', plan)}"
    await message.answer(f"🎮 {plan['intent']} · {plan['format']}. Собираю игровой черновик.")
    try:
        post = await asyncio.to_thread(call_ai, instructions, f"Тема игрового текста: {topic}", 700, OPENAI_POST_MODEL)
        draft_id = save_draft(
            "gaming", f"Gaming: {topic[:120]}", post,
            "VOID gaming vertical", f"manual://gaming/{now_iso()}", "GAMING", 7,
        )
        record_content_signature(plan, topic, draft_id)
    except Exception as exc:
        await message.answer(f"Игровой черновик не получился: {type(exc).__name__}: {exc}")
        return
    await message.answer(
        f"Черновик создан: #{draft_id}\n/preview {draft_id}\n/publish {draft_id}\n\n"
        "Игровая автопубликация пока выключена."
    )


@router.message(Command("gaming"))
async def gaming_command(message: Message):
    await gaming_draft(message, commercial=False)


@router.message(Command("gaming_commercial"))
async def gaming_commercial_command(message: Message):
    await gaming_draft(message, commercial=True)


@router.message(Command("archive"))
async def archive_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /archive несколько сигналов или итог наблюдений")
        return
    await manual_like(message, "archive", parts[1])


@router.message(Command("vault"))
async def vault_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /vault важная мысль для памяти VOID")
        return
    await manual_like(message, "vault", parts[1])


@router.message(Command("digest"))
async def digest_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Собираю дайджест. Да, это когда шуму выдают табель успеваемости.")
    items = await asyncio.to_thread(fetch_news)
    top = items[:5]
    if not top:
        await message.answer("Не нашёл новостей для дайджеста.")
        return

    content = "\n\n".join(
        [
            f"{i+1}. {item['title']}\n{item.get('summary','')}\nИсточник: {item.get('source_name','')} — {item.get('url','')}"
            for i, item in enumerate(top)
        ]
    )
    draft_id = await generate_and_save("digest", content, "DIGEST", "VOID sources", "")
    await message.answer(f"Дайджест создан: #{draft_id}\n/preview {draft_id}\n/publish {draft_id}")


@router.message(Command("drafts"))
async def drafts_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    rows = list_drafts(10)
    if not rows:
        await message.answer("Черновиков нет.")
        return

    lines = ["Черновики:"]
    for r in rows:
        lines.append(f"#{r['id']} · {r['mode']} · {r['frequency']}\n{r['title']}\n/preview {r['id']}")
    await message.answer("\n\n".join(lines))


@router.message(Command("preview"))
async def preview_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Используй: /preview ID")
        return

    draft = get_draft(int(parts[1]))
    if not draft:
        await message.answer("Черновик не найден.")
        return
    if draft["published_at"]:
        await message.answer("Этот текст уже опубликован. Для разговора нужна новая неопубликованная мысль VOID.")
        return

    await message.answer(draft["post"])


@router.message(Command("publish"))
async def publish_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Используй: /publish ID")
        return

    result = await publish_draft(bot, int(parts[1]))
    await message.answer(result)


@router.message(Command("publish_vk"))
async def publish_vk_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    parts = (message.text or "").split()
    force = False
    if len(parts) >= 2 and parts[1] == "--yes":
        force = True
        parts.pop(1)

    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Use: /publish_vk ID\nPublish for real: /publish_vk --yes ID")
        return

    result = await publish_draft_to_vk(int(parts[1]), force=force)
    await message.answer(result)


@router.message(Command("rubric_schedule"))
async def rubric_schedule_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer(rubric_schedule_text())


@router.message(Command("vk_schedule_draft"))
async def vk_schedule_draft_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Выбираю рубрику по московскому времени и собираю VK-черновик.")
    try:
        result = await make_scheduled_rubric_draft_once()
    except Exception as e:
        await message.answer(f"VK scheduled draft failed: {type(e).__name__}: {e}")
        return
    await message.answer(
        f"{result}\n"
        "Auto-publish locally:\n"
        "python vk_browser_publisher.py publish-draft ID"
    )


@router.message(Command("vk_music_status"))
async def vk_music_status_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    tracks = await asyncio.to_thread(load_vk_music_tracks)
    await message.answer(f"VK music tracks: {len(tracks)}\nFile: {VK_MUSIC_TRACKS_FILE}")


@router.message(Command("vk_music_import"))
async def vk_music_import_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    payload = (message.text or "").split(maxsplit=1)
    text = payload[1].strip() if len(payload) > 1 else ""
    if not text:
        await message.answer(
            "Use:\n"
            "/vk_music_import Artist - Track | https://vk.com/audio... | future, night\n"
            "One track per line."
        )
        return

    added, total = await asyncio.to_thread(import_vk_music_tracks, text)
    await message.answer(f"VK music import: added={added}, total={total}")


@router.message(Command("vk_music_sync"))
async def vk_music_sync_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    payload = (message.text or "").split(maxsplit=2)
    if len(payload) < 2 or not payload[1].startswith("http"):
        await message.answer(
            "Use:\n"
            "/vk_music_sync URL\n"
            "/vk_music_sync URL night,electronic,melancholy\n\n"
            "Browser playlist sync is the next VK automation step; this command is reserved for it."
        )
        return

    url = payload[1].strip()
    tags = payload[2].strip() if len(payload) > 2 else ""
    await message.answer(
        "VK music sync queued conceptually, not running yet.\n"
        f"URL: {url}\n"
        f"Base tags: {tags or 'music,culture,night'}\n\n"
        "Next implementation step: authorized browser scraper for VK playlists."
    )


@router.message(Command("vk_status"))
async def vk_status_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer(
        "VK publisher\n\n"
        f"VK_GROUP_ID: {'задан' if VK_GROUP_ID else 'не задан'}\n"
        f"VK_USER_ACCESS_TOKEN: {'задан' if VK_USER_ACCESS_TOKEN else 'не задан'}\n"
        f"VK_PHOTO_ACCESS_TOKEN: {'задан' if VK_PHOTO_ACCESS_TOKEN else 'не задан'}\n"
        f"VK_API_VERSION: {VK_API_VERSION}\n"
        f"VK_DRY_RUN: {VK_DRY_RUN}\n\n"
        "Проверка: /vk_test текст\n"
        "Тест черновика: /publish_vk ID\n"
        "Реальная публикация: /publish_vk --yes ID\n"
        "Реальная тест-публикация: /vk_test --yes текст"
    )


@router.message(Command("vk_test"))
async def vk_test_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    payload = (message.text or "").split(maxsplit=1)
    text = payload[1].strip() if len(payload) > 1 else ""
    force = False
    if text.startswith("--yes "):
        force = True
        text = text.removeprefix("--yes ").strip()

    if len(text) < 3:
        await message.answer("Используй: /vk_test текст\nРеально опубликовать: /vk_test --yes текст")
        return

    try:
        result = await asyncio.to_thread(post_to_vk_wall, text, force=force)
    except Exception as e:
        await message.answer(f"VK publish failed: {type(e).__name__}: {e}")
        return

    status = "dry-run" if result.get("dry_run") else "published"
    post_id = result.get("post_id")
    await message.answer(f"VK {status}. post_id={post_id}")


@router.message(Command("cross_status"))
async def cross_status_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer(crosspost_status_text())


@router.message(Command("void"))
async def void_crosspost_draft_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    fragment = extract_void_fragment_payload(message)
    if len(fragment) < 20:
        await message.answer("Используй: /void текст поста VOID или ответь /void на сообщение VOID.")
        return

    ok, reason = validate_void_fragment_for_naz(fragment)
    if not ok:
        await message.answer(f"Остановил черновик: {reason}. Сначала очисти входной текст.")
        return

    await message.answer("Вынимаю из текста фрагмент внутреннего разговора Void и Naz.")
    try:
        extracted = await asyncio.to_thread(build_void_fragment_for_naz_sync, fragment)
    except ValueError as e:
        await message.answer(f"Фрагмент остановлен: {e}.")
        return
    await message.answer(
        f"Голос Void (ещё не пост для Naz):\n\n{extracted}\n\n"
        "Чтобы передать на адаптацию через exchange: /publish_void с этим текстом."
    )


@router.message(Command("publish_void"))
@router.message(Command("thought_to_naz"))
async def publish_void_crosspost_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    if not can_crosspost("void_to_naz"):
        await message.answer(f"Лимит VOID -> Naz AI Bot на сегодня уже выбран: {CROSSPOST_DAILY_LIMIT}.")
        return

    fragment = extract_void_fragment_payload(message)
    if len(fragment) < 20:
        await message.answer("Используй: /thought_to_naz неопубликованная мысль VOID для Naz.")
        return

    ok, reason = validate_void_fragment_for_naz(fragment)
    if not ok:
        await message.answer(f"Передача остановлена: {reason}. Сначала очисти входной текст.")
        return

    await message.answer("Вынимаю голос Void и передаю Naz через exchange для отдельной интерпретации.")
    try:
        extracted = await asyncio.to_thread(build_void_fragment_for_naz_sync, fragment)
        path = queue_void_fragment_for_naz(
            extracted,
            source_event="manual_void_fragment",
        )
    except ValueError as e:
        await message.answer(f"Передача остановлена: {e}.")
        return
    await message.answer(
        f"Фрагмент передан в exchange, но не опубликован Void-проектом в Naz.\n"
        f"Файл: {path.name}\n"
        f"VOID -> Naz AI Bot: {crosspost_count('void_to_naz')}/{CROSSPOST_DAILY_LIMIT}"
    )


@router.message(Command("cross_to_naz"))
async def cross_to_naz_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    if not can_crosspost("void_to_naz"):
        await message.answer(f"Лимит VOID -> Naz AI Bot на сегодня уже выбран: {CROSSPOST_DAILY_LIMIT}.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Используй: /cross_to_naz ID")
        return

    draft = get_draft(int(parts[1]))
    if not draft:
        await message.answer("Черновик не найден.")
        return

    await message.answer("Вынимаю из VOID-черновика реплику для внутреннего диалога и кладу в exchange.")
    try:
        extracted = await asyncio.to_thread(build_crosspost_to_naz_sync, draft)
        path = queue_void_fragment_for_naz(
            extracted,
            source_event="manual_void_draft",
            topic=str(draft["title"] or ""),
        )
    except ValueError as e:
        await message.answer(f"Передача остановлена: {e}. Сначала проверь черновик и exchange.")
        return
    await message.answer(
        f"Готово: {path.name}. Naz должен сам расшифровать реплику перед публикацией.\n"
        f"VOID -> Naz AI Bot: {crosspost_count('void_to_naz')}/{CROSSPOST_DAILY_LIMIT}"
    )


@router.message(Command("cross_from_naz"))
@router.message(Command("thought_from_naz"))
async def cross_from_naz_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    if not can_crosspost("naz_to_void"):
        await message.answer(f"Лимит Naz AI Bot -> VOID на сегодня уже выбран: {CROSSPOST_DAILY_LIMIT}.")
        return

    parts = (message.text or "").split(maxsplit=1)
    source_text = parts[1].strip() if len(parts) >= 2 else ""
    if not source_text and message.reply_to_message:
        source_text = (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or ""
        ).strip()

    if len(source_text) < 20:
        await message.answer("Используй: /thought_from_naz неопубликованная мысль Naz")
        return

    await message.answer("VOID переваривает приватную мысль Naz и собирает собственный выпуск рубрики.")
    adapted = await asyncio.to_thread(build_crosspost_from_naz_sync, source_text)
    draft_id = save_draft(
        adapted["mode"],
        adapted["title"],
        adapted["post"],
        "Naz AI Bot",
        f"manual://cross/naz/{now_iso()}",
        "crosspost",
        publish_score=8,
    )
    result = await publish_draft(bot, draft_id)
    if result.startswith("Опубликовано:"):
        mark_crosspost("naz_to_void")
        result = f"{result}\nNaz AI Bot -> VOID: {crosspost_count('naz_to_void')}/{CROSSPOST_DAILY_LIMIT}"
    await message.answer(result)


@router.message(Command("autopost_now"))
async def autopost_now_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Запускаю автопостинг один раз. VOID надевает редакторские перчатки.")
    result = await autopost_once(bot)
    await message.answer(result)


@router.message(Command("void_now"))
async def void_now_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Запускаю не новость, а VOID-сигнал из контент-плана.")
    result = await autopost_void_signal_once(bot)
    await message.answer(result)


@router.message(Command("telegram_schedule"))
async def telegram_schedule_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer(telegram_schedule_text())


@router.message(Command("void_schedule_now"))
async def void_schedule_now_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Запускаю scheduled-рубрику VOID для Telegram.")
    result = await publish_telegram_void_scheduled_once(bot)
    await message.answer(result)


@router.message(Command("auto_on"))
async def auto_on_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    set_setting("auto_publish", "1")
    await message.answer(
        "Автопубликация включена.\n"
        "VOID будет публиковать scheduled-рубрику примерно раз в 3 часа.\n"
        "Naz живёт в своём проекте; здесь остаётся только обмен через exchange/adaptation.\n"
        "Если он начнёт душнить — это всё ещё наша ответственность. Увы, зрелость."
    )


@router.message(Command("auto_off"))
async def auto_off_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    set_setting("auto_publish", "0")
    await message.answer("Автопубликация выключена. VOID снова смотрит на мир, но держит руки при себе.")


@router.message(Command("auto_status"))
async def auto_status_command(message: Message):
    enabled = get_setting("auto_publish", "0") == "1"
    await message.answer(
        f"Автопубликация: {'включена' if enabled else 'выключена'}\n\n"
        f"Расписание (Europe/Moscow): {', '.join(VOID_TELEGRAM_AUTO_TIMES) or 'не задано'}\n"
        f"Последний слот VOID: {get_setting('telegram_void_last_slot', '—')}"
    )


@router.callback_query(F.data.startswith("catch:"))
async def catch_callback(callback: CallbackQuery):
    draft_id = int(callback.data.split(":", 1)[1])
    conn = None

    try:
        conn = db()

        conn.execute(
            "INSERT INTO catches(draft_id, user_id, username, created_at) VALUES (?, ?, ?, ?)",
            (
                draft_id,
                callback.from_user.id,
                callback.from_user.username,
                now_iso(),
            ),
        )

        conn.commit()
        await callback.answer("Поймал.")

    except sqlite3.IntegrityError:
        await callback.answer("Уже поймал этот сигнал.", show_alert=True)

    finally:
        if conn:
            conn.close()


@router.callback_query(F.data == "void:quick:news")
async def void_quick_news_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "📰 Новости\n\n"
        "Пришли ссылку, заголовок или короткий пересказ новости. Я вытащу из неё сигнал, а не просто перескажу пресс-релиз.",
        reply_markup=quick_actions_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:quick:music")
async def void_quick_music_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🎵 Музыка и культура\n\n"
        "Отправляй мне новости о музыке, артистах или платформах — "
        "и я дам культурный взгляд на тему.",
        reply_markup=quick_actions_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "void:quick:future")
async def void_quick_future_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "🔮 Будущее\n\n"
        "Отправляй мне тему о будущем, технологиях или трендах — "
        "и я проанализирую грядущий сдвиг.",
        reply_markup=quick_actions_keyboard(),
    )
    await callback.answer()


async def handle_reply_button(message: Message, text: str) -> bool:
    if text == BTN_BACK:
        await message.answer(welcome_text(), reply_markup=reply_main_keyboard())
        return True

    if text == BTN_GUIDE:
        await message.answer(guide_text(), reply_markup=reply_guide_keyboard())
        return True

    if text == BTN_RESET:
        clear_dialog_context(message.from_user.id)
        await message.answer("🧹 Контекст очищен. Можно начинать с новой мысли.", reply_markup=reply_main_keyboard())
        return True

    if text == BTN_PERSONA:
        await message.answer("🎭 Выбери характер VOID:", reply_markup=persona_keyboard())
        return True

    if text == BTN_TOPICS:
        await message.answer("🛰 Выбери направление или просто пришли свою тему.", reply_markup=reply_topics_keyboard())
        return True

    if text == BTN_STATUS:
        session = get_dialog_session(message.from_user.id)
        status = (
            "📊 Статус VOID\n\n"
            f"Диалог: всегда ON\n"
            f"Характер: {session['personality']}"
        )
        if is_admin(message):
            status += f"\n\nКросспостинг:\n{crosspost_status_text()}"
        await message.answer(status, reply_markup=reply_main_keyboard())
        return True

    if text == "📰 Новости":
        await message.answer(
            "📰 Пришли ссылку, заголовок или короткий пересказ новости. Я вытащу из неё сигнал.",
            reply_markup=reply_topics_keyboard(),
        )
        return True

    if text == "🎵 Музыка":
        await message.answer(
            "🎵 Пришли новость про музыку, артистов или платформы. Я посмотрю на культурный сдвиг.",
            reply_markup=reply_topics_keyboard(),
        )
        return True

    if text == "🔮 Будущее":
        await message.answer(
            "🔮 Пришли тему про технологии, тренды или странное будущее. Я найду, куда движется сигнал.",
            reply_markup=reply_topics_keyboard(),
        )
        return True

    return False


SUPPORTED_AUDIO_SUFFIXES = {".aac", ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".wav", ".webm"}


def sanitize_voice_text(text: str) -> str:
    """Remove lightweight Markdown that should not be spoken aloud."""
    clean = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", str(text or ""))
    clean = clean.replace("**", "").replace("__", "").replace("```", "")
    return clean.strip()


def telegram_audio_name(message: Message, media: Any) -> str:
    """Build a safe filename so the transcription API can detect the format."""
    if getattr(message, "voice", None) is media:
        return "telegram-voice.ogg"
    original = Path(str(getattr(media, "file_name", "") or "")).name
    suffix = Path(original).suffix.lower()
    if suffix not in SUPPORTED_AUDIO_SUFFIXES:
        mime_suffix = {
            "audio/aac": ".aac",
            "audio/flac": ".flac",
            "audio/m4a": ".m4a",
            "audio/mp4": ".mp4",
            "audio/mpeg": ".mp3",
            "audio/ogg": ".ogg",
            "audio/wav": ".wav",
            "audio/webm": ".webm",
        }.get(str(getattr(media, "mime_type", "") or "").lower())
        suffix = mime_suffix or ""
    if not suffix:
        raise ValueError("Не удалось определить формат аудио. Пришли voice, MP3, M4A, WAV, OGG или WEBM.")
    return f"telegram-audio{suffix}"


async def download_telegram_audio(message: Message) -> tuple[bytes, str]:
    media = getattr(message, "voice", None) or getattr(message, "audio", None)
    if not media:
        raise ValueError("В сообщении нет голосового или аудиофайла.")
    if getattr(media, "file_size", 0) and media.file_size > VOICE_MAX_BYTES:
        raise ValueError(f"Аудио превышает лимит {VOICE_MAX_BYTES // (1024 * 1024)} МБ.")
    if getattr(media, "duration", 0) and media.duration > VOICE_MAX_DURATION_SECONDS:
        raise ValueError(f"Голосовое длиннее {VOICE_MAX_DURATION_SECONDS // 60} минут.")
    filename = telegram_audio_name(message, media)
    payload = BytesIO()
    await message.bot.download(media, destination=payload)
    data = payload.getvalue()
    if not data:
        raise ValueError("Telegram вернул пустой аудиофайл.")
    if len(data) > VOICE_MAX_BYTES:
        raise ValueError(f"Аудио превышает лимит {VOICE_MAX_BYTES // (1024 * 1024)} МБ.")
    return data, filename


async def transcribe_voice_bytes(data: bytes, filename: str) -> str:
    def _request() -> str:
        payload = BytesIO(data)
        payload.name = filename
        response = ensure_voice_openai_client().audio.transcriptions.create(
            model=OPENAI_TRANSCRIBE_MODEL,
            file=payload,
        )
        return str(getattr(response, "text", "") or "").strip()

    try:
        transcript = await asyncio.to_thread(_request)
    except Exception as exc:
        print(f"Voice transcription failed: {type(exc).__name__}", flush=True)
        raise RuntimeError("Не удалось распознать голосовое. Попробуй ещё раз позже.") from exc
    if not transcript:
        raise RuntimeError("Не удалось расслышать речь в голосовом.")
    return transcript


async def synthesize_voice_bytes(text: str) -> bytes:
    clean_text = sanitize_voice_text(text)
    if not clean_text:
        raise ValueError("Нечего озвучивать.")

    def _request() -> bytes:
        response = ensure_voice_openai_client().audio.speech.create(
            model=OPENAI_TTS_MODEL,
            voice=OPENAI_TTS_VOICE,
            input=clean_text,
            instructions=(
                "Speak naturally in Russian as VOID: observant, calm, concise, lightly ironic, "
                "never theatrical. This is an AI-generated voice."
            ),
            response_format="opus",
        )
        content = response.read() if hasattr(response, "read") else getattr(response, "content", b"")
        return bytes(content or b"")

    try:
        audio = await asyncio.to_thread(_request)
    except Exception as exc:
        print(f"Voice synthesis failed: {type(exc).__name__}", flush=True)
        raise RuntimeError("Не удалось озвучить ответ.") from exc
    if not audio:
        raise RuntimeError("OpenAI вернул пустой голосовой ответ.")
    return audio


async def generate_dialog_answer(user_id: int, text: str) -> str:
    session = get_dialog_session(user_id)
    history = get_dialog_context(user_id, limit=8)
    personality = session.get("personality", "observer")
    memory_note = get_dialog_memory(user_id)
    prompt = build_dialog_prompt(text, personality, history, memory_note)
    reply = await asyncio.to_thread(
        call_ai,
        prompt,
        text,
        model=OPENAI_DIALOG_MODEL,
    )
    reply = (reply or "").strip() or "Я не успел сформулировать ответ. Попробуй ещё раз."
    save_dialog_message(user_id, "user", text)
    save_dialog_message(user_id, "assistant", reply)
    new_memory = build_memory_note(text, reply)
    if new_memory:
        save_dialog_message(user_id, "memory", new_memory)
    return reply


@router.message(F.voice | F.audio)
async def handle_voice_message(message: Message) -> None:
    if not message.from_user:
        return
    if not VOICE_MESSAGES_ENABLED:
        await message.answer("Голосовые VOID пока выключены в настройках.")
        return
    if VOICE_MESSAGES_ADMIN_ONLY and not is_admin(message):
        await message.answer("Голосовой режим пока доступен только администратору VOID.")
        return
    if not OPENAI_VOICE_API_KEY:
        await message.answer("Голосовой API ещё не настроен. Нужен отдельный официальный OpenAI key.")
        return

    try:
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        data, filename = await download_telegram_audio(message)
        transcript = await transcribe_voice_bytes(data, filename)
        answer = sanitize_voice_text(await generate_dialog_answer(message.from_user.id, transcript))
        await message.bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.RECORD_VOICE)
        try:
            audio = await synthesize_voice_bytes(answer)
        except RuntimeError:
            print("Voice reply falling back to text", flush=True)
            await message.answer(answer, reply_markup=reply_main_keyboard())
            return

        try:
            await message.answer_voice(
                voice=BufferedInputFile(audio, filename="void-reply.ogg"),
                caption="AI-голос VOID",
                reply_markup=reply_main_keyboard(),
            )
        except TelegramBadRequest:
            await message.answer_document(
                document=BufferedInputFile(audio, filename="void-reply.ogg"),
                caption="Голосовой ответ VOID",
                reply_markup=reply_main_keyboard(),
            )
    except (ValueError, RuntimeError) as exc:
        await message.answer(sanitize_voice_text(str(exc)), reply_markup=reply_main_keyboard())
    except Exception as exc:
        print(f"Voice message failed: {type(exc).__name__}", flush=True)
        await message.answer("Голосовой режим временно недоступен.", reply_markup=reply_main_keyboard())


@router.message()
async def free_text_handler(message: Message):

    chat_id = message.chat.id
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if await handle_delegated_reply(message, text):
        return

    if is_admin(message) and message.reply_to_message:
        try:
            named = name_contact_from_reply(message.reply_to_message.message_id, user_id, text)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if named:
            await message.answer(
                f"Записал: {named['alias']} → {named['display_name']}. Теперь можно сказать: «Напиши {named['alias']}, чтобы…»"
            )
            return

    if is_admin(message):
        try:
            request = delegated_messaging.parse_delegation_request(text)
        except ValueError as exc:
            await message.answer(str(exc))
            return
        if request:
            spoken_alias, purpose = request
            contacts = list_saved_contacts(user_id)
            contact = delegated_messaging.resolve_saved_contact(contacts, spoken_alias)
            if not contact:
                aliases = ", ".join(str(item["alias"]) for item in contacts) or "пока пусто"
                await message.answer(f"Не нашёл один точный контакт «{spoken_alias}». Сохранены: {aliases}.")
                return
            try:
                await start_saved_contact_delegation(message, contact, purpose)
            except ValueError as exc:
                await message.answer(str(exc))
            return

    if user_id != ADMIN_ID:
        remember_reachable_peer(
            user_id, message.from_user.full_name,
            (delegated_messaging.utc_now() + timedelta(hours=24)).isoformat(timespec="seconds"),
        )
        await ensure_contact_named(message)

    if await handle_reply_button(message, text):
        return

    if not text:
        return

    try:
        reply = await generate_dialog_answer(user_id, text)
    except Exception as e:
        print("DIALOG AI ERROR:", repr(e))
        reply = f"AI ERROR: {e}"

    await message.answer(reply, reply_markup=reply_main_keyboard())


def validate_editorial_config() -> None:
    if editorial_policy.EDITORIAL_CONTRACT_VERSION != "editorial-relevance.v1":
        raise RuntimeError("unknown editorial contract version")
    if editorial_policy.PERSONA_POLICY_VERSION != "void-persona.v2":
        raise RuntimeError("unknown VOID persona policy version")
    if editorial_policy.VISUAL_CODE_VERSION != "void-visual.v2":
        raise RuntimeError("unknown VOID visual code version")
    names = [str(item.get("name") or "").strip() for item in (*RUBRIC_SCHEDULE, *TELEGRAM_VOID_SCHEDULE)]
    if any(not name for name in names):
        raise RuntimeError("VOID rubric registry contains an unnamed rubric")


async def run_bot_once():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    validate_editorial_config()
    init_db()

    session = AiohttpSession(proxy=TELEGRAM_PROXY_URL or None)
    session._connector_init["family"] = socket.AF_INET
    bot = Bot(token=BOT_TOKEN, session=session)
    dp = Dispatcher()
    dp.include_router(router)

    global auto_task, exchange_task
    auto_task = asyncio.create_task(auto_loop(bot))
    exchange_task = asyncio.create_task(exchange_loop(bot))

    print("POLLING START", flush=True)

    try:
        await dp.start_polling(
            bot,
            allowed_updates=["message", "callback_query"],
        )
    finally:
        if auto_task:
            auto_task.cancel()
        if exchange_task:
            exchange_task.cancel()
        await bot.session.close()


async def main():
    await run_bot_once()


if __name__ == "__main__":
    asyncio.run(main())
