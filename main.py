
from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import re
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import feedparser
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, KeyboardButton, Message, ReplyKeyboardMarkup
from dotenv import load_dotenv

from void_core import CONTENT_PLAN, MODE_RUBRICS, VOID_CORE_PROMPT, platform_context

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
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_IMAGE_SIZE = os.getenv("OPENAI_IMAGE_SIZE", "1024x1024")
OPENAI_IMAGE_QUALITY = os.getenv("OPENAI_IMAGE_QUALITY", "medium")
TELEGRAM_PROXY_URL = os.getenv("TELEGRAM_PROXY_URL", "")

NAZ_CHANNEL_ID = os.getenv("NAZ_CHANNEL_ID", "")
NAZ_BOT_TOKEN = os.getenv("NAZ_BOT_TOKEN", "")
CROSSPOST_DAILY_LIMIT = int(os.getenv("CROSSPOST_DAILY_LIMIT", "2") or "2")
CROSSPOST_EXCHANGE_ENABLED = os.getenv("CROSSPOST_EXCHANGE_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
CROSSPOST_EXCHANGE_AUTO_PUBLISH = os.getenv("CROSSPOST_EXCHANGE_AUTO_PUBLISH", "true").strip().lower() not in {"0", "false", "no", "off"}
CROSSPOST_EXCHANGE_DIR = Path(os.getenv("CROSSPOST_EXCHANGE_DIR", "/opt/bot_exchange").strip())
CROSSPOST_EXCHANGE_INTERVAL_SECONDS = max(60, int(os.getenv("CROSSPOST_EXCHANGE_INTERVAL_SECONDS", "300") or "300"))
CROSSPOST_EXCHANGE_MAX_PER_RUN = max(1, min(int(os.getenv("CROSSPOST_EXCHANGE_MAX_PER_RUN", "1") or "1"), 5))

DB_PATH = "void.db"

router = Router()
auto_task: asyncio.Task | None = None
exchange_task: asyncio.Task | None = None


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
        f"NAZ_CHANNEL_ID: {'set' if NAZ_CHANNEL_ID else 'not set'}"
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

    return f"""
{VOID_CORE_PROMPT}

{platform_context("telegram")}

    Ты VOID Entity.
    Ты — наблюдательный, сухой, чуть ироничный собеседник.
    Отвечай кратко, по-русски, без markdown.
    {personality_style}
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


def save_draft(mode: str, title: str, post: str, source_name: str = "", source_url: str = "", frequency: str = "", publish_score: int = 5) -> int:
    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO drafts(mode, title, post, source_name, source_url, frequency, publish_score, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (mode, title, post, source_name, source_url, frequency, int(publish_score), now_iso()),
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


def mark_published(draft_id: int, source_url: str = "") -> None:
    conn = db()
    conn.execute("UPDATE drafts SET published_at=? WHERE id=?", (now_iso(), draft_id))
    if source_url and source_url.startswith("http"):
        conn.execute(
            "INSERT OR REPLACE INTO published_urls(url, draft_id, published_at) VALUES (?, ?, ?)",
            (source_url, draft_id, now_iso()),
        )
    conn.commit()
    conn.close()


def already_published(url: str) -> bool:
    if not url or not url.startswith("http"):
        return False
    conn = db()
    row = conn.execute("SELECT url FROM published_urls WHERE url=?", (url,)).fetchone()
    conn.close()
    return row is not None


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


def call_ai(
    instructions: str,
    input_text: str,
    max_output_tokens: int = 200,
    model: str | None = None,
) -> str:
    client = openai_client()

    response = client.responses.create(
        model=model or OPENAI_MODEL,
        instructions=instructions,
        input=input_text,
        max_output_tokens=max_output_tokens,
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
    if mode == "digest":
        return 2
    if len(text) > 1100 and len(re.findall(r"\n\s*\d+[\.\)]", text)) >= 2:
        return 2
    return 1


def build_image_prompts_sync(draft: dict | sqlite3.Row) -> list[str]:
    mode = draft["mode"] or "news"
    title = draft["title"] or "VOID signal"
    post = draft["post"] or ""
    source_name = draft["source_name"] or ""
    count = image_count_for_draft(mode, post)

    instructions = """
You are an art director for a Telegram channel called VOID.
Create visual prompts for image generation that match the post exactly.
Return only lines in this format:
IMAGE: prompt

Rules:
- Return 1 or 2 IMAGE lines, no extra text.
- The image must be relevant to the post's concrete topic.
- Avoid text, logos, UI screenshots, brand marks, and fake article pages.
- Avoid depicting a real named person unless the post is specifically about that person.
- Style: editorial conceptual illustration, cinematic but clean, dark neutral background, high contrast, subtle technological atmosphere.
- No gore, no explicit content.
""".strip()

    input_text = (
        f"NEEDED_IMAGES: {count}\n"
        f"MODE: {mode}\n"
        f"TITLE: {title}\n"
        f"SOURCE_NAME: {source_name}\n"
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
            return prompts
    except Exception as e:
        print(f"image prompt error: {type(e).__name__}: {e}", flush=True)

    fallback = (
        f"Editorial conceptual illustration for a Telegram post titled '{title}'. "
        f"Represent the concrete topic of the post, source context: {source_name}. "
        "No text, no logos, no UI screenshots, dark neutral background, high contrast, clean cinematic composition."
    )
    return [fallback] * count


def generate_post_images_sync(draft: dict | sqlite3.Row) -> list[bytes]:
    client = openai_client()
    images: list[bytes] = []

    for prompt in build_image_prompts_sync(draft):
        response = client.images.generate(
            model=OPENAI_IMAGE_MODEL,
            prompt=prompt,
            size=OPENAI_IMAGE_SIZE,
            quality=OPENAI_IMAGE_QUALITY,
            n=1,
        )
        if not response.data:
            continue

        b64_json = getattr(response.data[0], "b64_json", None)
        if not b64_json:
            continue

        images.append(base64.b64decode(b64_json))

    return images[:2]


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
You adapt a VOID fragment for the Naz AI Bot Telegram channel.

VOID source voice:
{VOID_CORE_PROMPT}

Naz AI Bot voice:
- practical AI ecosystem, tools, automation, content systems, useful experiments;
- clear, friendly, not mystical;
- show where this VOID signal has practical meaning;
- explain the trap or use case for AI, bots, content, development, projects, or a person building systems;
- do not mirror the post word-for-word;
- choose one format naturally: "VOID сказал", "Перевод с VOID на человеческий", or "Спор двух ботов";
- vary the opening phrase;
- mention VOID as the source of the signal;
- write the Naz comment in first person: "я вижу", "я бы добавил", "для меня тут важно";
- never write "Naz thinks", "Naz считает", "комментарий Naz", or describe Naz in third person;
- stop if the input contains secrets, tokens, passwords, private URLs, SSH/IP access, client details, or private chats;
- Russian only.

Return only the final Telegram post. No markdown fences.
""".strip()

    input_text = f"VOID_FRAGMENT:\n{fragment.strip()}"
    return trim_post(call_ai(instructions, input_text, max_output_tokens=900, model=OPENAI_POST_MODEL), limit=3000)


def build_crosspost_from_naz_sync(source_text: str) -> dict[str, str]:
    instructions = f"""
You adapt a Naz AI Bot post for the VOID Signals Telegram channel.

VOID core:
{VOID_CORE_PROMPT}

Task:
- Do not repost literally.
- Translate practical AI/tool content into a VOID observation about the human in a digital world.
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

    raw = call_ai(instructions, f"NAZ_AI_BOT_POST:\n{source_text}", max_output_tokens=1100, model=OPENAI_POST_MODEL)
    title, post = parse_ai_output(raw)
    mode_match = re.search(r"MODE\s*:\s*(signal|observation|future|vault)", raw, flags=re.I)
    mode = mode_match.group(1).lower() if mode_match else "observation"
    post = clean_source_lines(post)
    if "Источник:" not in post:
        post = f"{post.rstrip()}\n\nИсточник: Naz AI Bot"
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


def write_exchange_payload(direction: str, payload: dict[str, str]) -> Path | None:
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


def queue_void_post_for_naz(draft: dict | sqlite3.Row) -> None:
    if not CROSSPOST_EXCHANGE_ENABLED:
        return
    source_name = str(draft["source_name"] or "")
    source_url = str(draft["source_url"] or "")
    frequency = str(draft["frequency"] or "")
    if source_name == "Naz AI Bot" or frequency == "crosspost" or source_url.startswith("manual://cross/naz/"):
        return
    post = str(draft["post"] or "").strip()
    if len(post) < 40:
        return
    write_exchange_payload(
        "void_to_naz",
        {
            "source": "void_entity",
            "source_event": "published_draft",
            "topic": str(draft["title"] or ""),
            "text": post[:6000],
            "publish_mode": "auto" if CROSSPOST_EXCHANGE_AUTO_PUBLISH else "draft",
        },
    )


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

            adapted = await asyncio.to_thread(build_crosspost_from_naz_sync, source_text)
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
    if len(post.strip()) < 250:
        return False, "слишком коротко"
    if len(post) > 3600:
        return False, "слишком длинно"
    if too_much_english(post):
        return False, "слишком много английского"
    if "Источник:" not in post and "manual://" not in post:
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


def build_prompt(mode: str, frequency: str = "HUMAN") -> str:
    rubric = MODE_RUBRICS.get(mode, "SIGNAL")

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
        "news": "1. Заголовок рубрики: {rubric} / {frequency} если частота уместна, иначе просто {rubric}\n2. Факт / мысль / наблюдение.\n3. Что это говорит о человеке в цифровой среде.\n4. VOID COMMENT: коротко, иронично, не душно.\n5. Источник, если источник есть.",
        "manual": "1. Заголовок рубрики: {rubric}\n2. Мысль автора в собственной форме.\n3. Наблюдение о том, что это значит для человека.\n4. VOID COMMENT: коротко, без пафоса.\n5. Источник, если источник есть.",
        "midnight": "1. Заголовок рубрики: {rubric}\n2. Ночная, чуть более тёмная мысль.\n3. Ощущение, которое возникает в человеке рядом с этой темой.\n4. VOID COMMENT: короткий, холодный, точный.\n5. Источник, если источник есть.",
        "observation": "1. Заголовок рубрики: {rubric}\n2. Короткое наблюдение над явлением.\n3. Что это говорит о привычке, платформе или поведении.\n4. VOID COMMENT: сухо, без лишней драматизации.\n5. Источник, если источник есть.",
        "culture": "1. Заголовок рубрики: {rubric}\n2. Культурное наблюдение над явлением.\n3. Что это говорит о людях, привычке, музыке, медиа или атмосфере.\n4. VOID COMMENT: чуть ближе к человеку, без пафоса.\n5. Источник, если источник есть.",
        "future": "1. Заголовок рубрики: {rubric}\n2. Сдвиг, который уже заметен.\n3. Как это меняет поведение или среду.\n4. VOID COMMENT: чуть аналитичнее, но живо.\n5. Источник, если источник есть.",
        "digest": "1. Заголовок рубрики: {rubric}\n2. 3–5 сигналов в одном посте.\n3. Общий вывод по теме.\n4. VOID COMMENT: ироничный, краткий, связующий.\n5. Источник, если источник есть.",
    }

    structure.setdefault("signal", structure["manual"])
    structure.setdefault("frequency", "1. ???????: {rubric}\n2. ?????????, ????, ????? ??? ?????????? ?????.\n3. ??? ??? ?????? ? ????????? ? ?????????.\n4. VOID COMMENT: ???????, ??? ????????? ??????.")
    structure.setdefault("archive", "1. ???????: {rubric}\n2. 3-5 ????????? ????????.\n3. ????? ?????: ??? ????? ???? ????????? ??????.\n4. VOID COMMENT: ????? ???????? ??? ??????.")
    structure.setdefault("vault", "1. ???????: {rubric}\n2. ??????? ?????.\n3. ?????? ??? ????? ??? ???????? ? ???????? ?????.\n4. ??? ????? ?????????.\n5. VOID COMMENT: ??? ??????, ?? ? ?????.")

    return f"""
{VOID_CORE_PROMPT}

{platform_context("telegram")}

Ты — редактор Telegram-канала VOID.

Пиши СТРОГО НА РУССКОМ.
Голос VOID: умный, живой, наблюдательный, с сухим юмором.
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


def generate_post_sync(mode: str, content: str, frequency: str = "HUMAN", source_name: str = "", source_url: str = "") -> dict[str, Any]:
    prompt = build_prompt(mode, frequency)
    input_text = (
        f"MODE: {mode}\n"
        f"FREQUENCY: {frequency}\n"
        f"SOURCE_NAME: {source_name}\n"
        f"SOURCE_URL: {source_url}\n"
        f"CONTENT:\n{content}"
    )

    try:
        raw = call_ai(prompt, input_text, max_output_tokens=1200, model=OPENAI_POST_MODEL)
        title, post = parse_ai_output(raw)

        if too_much_english(post):
            raw = call_ai(
                prompt + "\n\nПредыдущий вариант оставил слишком много английского. Перепиши полностью по-русски.",
                input_text,
                max_output_tokens=1200,
                model=OPENAI_POST_MODEL,
            )
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
    }


async def generate_and_save(mode: str, content: str, frequency: str = "HUMAN", source_name: str = "", source_url: str = "") -> int:
    draft = await asyncio.to_thread(generate_post_sync, mode, content, frequency, source_name, source_url)
    return save_draft(
        mode=draft["mode"],
        title=draft["title"],
        post=draft["post"],
        source_name=draft["source_name"],
        source_url=draft["source_url"],
        frequency=draft["frequency"],
        publish_score=draft["publish_score"],
    )


def catch_keyboard(draft_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="поймал", callback_data=f"catch:{draft_id}")],
        ]
    )


async def publish_draft_images(bot: Bot, draft: dict | sqlite3.Row) -> tuple[int, str | None]:
    image_error: str | None = None

    try:
        images = await asyncio.to_thread(generate_post_images_sync, draft)
    except Exception as e:
        images = []
        image_error = f"{type(e).__name__}: {e}"

    if not images:
        source_image_url = await asyncio.to_thread(find_source_image_url, draft["source_url"] or "")
        if source_image_url:
            try:
                await bot.send_photo(chat_id=CHANNEL_ID, photo=source_image_url)
                return 1, None
            except Exception as e:
                fallback_error = f"{type(e).__name__}: {e}"
                return 0, f"{image_error}; source image fallback: {fallback_error}" if image_error else fallback_error
        return 0, image_error

    try:
        if len(images) == 1:
            await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=BufferedInputFile(images[0], filename=f"void-{draft['id']}-1.png"),
            )
        else:
            media = [
                InputMediaPhoto(
                    media=BufferedInputFile(image, filename=f"void-{draft['id']}-{index}.png")
                )
                for index, image in enumerate(images, start=1)
            ]
            await bot.send_media_group(chat_id=CHANNEL_ID, media=media)
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"

    return len(images), None


async def publish_draft(bot: Bot, draft_id: int) -> str:
    if not CHANNEL_ID:
        return "CHANNEL_ID не задан. Добавь канал в Secrets."

    draft = get_draft(draft_id)
    if not draft:
        return "Черновик не найден."

    ok, reason = quality_check(draft["post"])
    if not ok:
        return f"Не публикую: {reason}. Сначала /preview {draft_id}."

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=draft["post"],
        reply_markup=catch_keyboard(draft_id),
        disable_web_page_preview=True,
    )
    image_count, image_error = await publish_draft_images(bot, draft)
    mark_published(draft_id, draft["source_url"] or "")
    queue_void_post_for_naz(draft)
    if image_count:
        return f"Опубликовано: #{draft_id}. Картинок: {image_count}"
    if image_error:
        return f"Опубликовано: #{draft_id}. Картинки не приложились: {image_error}"
    return f"Опубликовано: #{draft_id}. Картинок: 0"


async def send_to_naz_channel(bot: Bot, text: str) -> None:
    if not NAZ_CHANNEL_ID:
        raise ValueError("NAZ_CHANNEL_ID is not set")

    if NAZ_BOT_TOKEN and NAZ_BOT_TOKEN != BOT_TOKEN:
        naz_bot = Bot(token=NAZ_BOT_TOKEN)
        try:
            await naz_bot.send_message(
                chat_id=NAZ_CHANNEL_ID,
                text=text,
                disable_web_page_preview=True,
            )
        finally:
            await naz_bot.session.close()
        return

    await bot.send_message(
        chat_id=NAZ_CHANNEL_ID,
        text=text,
        disable_web_page_preview=True,
    )


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
        content = (
            f"Заголовок: {item['title']}\n"
            f"Описание: {item.get('summary', '')}\n"
            f"Источник: {item.get('source_name', '')}\n"
            f"Ссылка: {item.get('url', '')}"
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
            result = await publish_draft(bot, draft_id)
            return f"Автопостинг: {result}"
        else:
            continue

    return "Автопостинг: сигналы были, но quality gate всё зарезал. Редкий случай, когда цензура оказалась полезной."


def next_content_plan_slot() -> tuple[int, dict[str, str]]:
    current = int(get_setting("auto_content_index", "0") or "0")
    slot = CONTENT_PLAN[current % len(CONTENT_PLAN)]
    set_setting("auto_content_index", str(current + 1))
    return current, slot


async def autopost_void_signal_once(bot: Bot) -> str:
    index, slot = await asyncio.to_thread(next_content_plan_slot)
    mode = slot["mode"]
    frequency = slot["frequency"]
    content = (
        f"CONTENT_PLAN_INDEX: {index}\n"
        f"RUBRIC: {slot['name']}\n"
        f"PLATFORM: Telegram\n"
        f"BRIEF:\n{slot['brief']}\n\n"
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

    result = await publish_draft(bot, draft_id)
    return f"VOID-план: {result}"


async def autopost_scheduled_once(bot: Bot) -> str:
    cycle = int(get_setting("auto_publish_cycle", "0") or "0")
    set_setting("auto_publish_cycle", str(cycle + 1))
    if cycle % 3 == 2:
        return await autopost_once(bot)
    return await autopost_void_signal_once(bot)


async def auto_loop(bot: Bot) -> None:
    while True:
        try:
            enabled = get_setting("auto_publish", "0") == "1"
            if enabled:
                now_ts = int(datetime.now(timezone.utc).timestamp())
                last_ts = int(get_setting("auto_publish_last_ts", "0") or "0")
                if now_ts - last_ts >= 60 * 60 * 3:
                    set_setting("auto_publish_last_ts", str(now_ts))
                    result = await autopost_scheduled_once(bot)
                    print(result, flush=True)
        except Exception as e:
            print(f"auto_loop error: {type(e).__name__}: {e}", flush=True)

        await asyncio.sleep(60 * 60 * 3)


@router.message(CommandStart())
async def start(message: Message):
    set_dialog_enabled(message.from_user.id, True)
    await message.answer(
        welcome_text(),
        reply_markup=reply_main_keyboard(),
    )
        
@router.message(Command("help"))
async def help_command(message: Message):
    await start(message)

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

    await message.answer("Собираю Naz-черновик из VOID-фрагмента.")
    adapted = await asyncio.to_thread(build_void_fragment_for_naz_sync, fragment)
    await message.answer(adapted)


@router.message(Command("publish_void"))
async def publish_void_crosspost_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    if not NAZ_CHANNEL_ID:
        await message.answer("NAZ_CHANNEL_ID не задан. Добавь канал Naz AI Bot в .env/Secrets.")
        return

    if not can_crosspost("void_to_naz"):
        await message.answer(f"Лимит VOID -> Naz AI Bot на сегодня уже выбран: {CROSSPOST_DAILY_LIMIT}.")
        return

    fragment = extract_void_fragment_payload(message)
    if len(fragment) < 20:
        await message.answer("Используй: /publish_void текст поста VOID или ответь /publish_void на сообщение VOID.")
        return

    ok, reason = validate_void_fragment_for_naz(fragment)
    if not ok:
        await message.answer(f"Автопубликация остановлена: {reason}. Сначала очисти входной текст.")
        return

    await message.answer("Готовлю Naz-кросспост и публикую.")
    adapted = await asyncio.to_thread(build_void_fragment_for_naz_sync, fragment)
    await send_to_naz_channel(bot, adapted)
    mark_crosspost("void_to_naz")
    await message.answer(f"Опубликовано в Naz AI Bot. VOID -> Naz AI Bot: {crosspost_count('void_to_naz')}/{CROSSPOST_DAILY_LIMIT}")


@router.message(Command("cross_to_naz"))
async def cross_to_naz_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    if not NAZ_CHANNEL_ID:
        await message.answer("NAZ_CHANNEL_ID не задан. Добавь канал Naz AI Bot в .env/Secrets.")
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

    await message.answer("Адаптирую VOID-сигнал под Naz AI Bot и отправляю.")
    try:
        adapted = await asyncio.to_thread(build_crosspost_to_naz_sync, draft)
    except ValueError as e:
        await message.answer(f"Автопубликация остановлена: {e}. Сначала очисти черновик.")
        return
    await send_to_naz_channel(bot, adapted)
    mark_crosspost("void_to_naz")
    await message.answer(f"Готово. VOID -> Naz AI Bot: {crosspost_count('void_to_naz')}/{CROSSPOST_DAILY_LIMIT}")


@router.message(Command("cross_from_naz"))
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
        await message.answer("Используй: /cross_from_naz текст поста Naz AI Bot")
        return

    await message.answer("Перевожу пост Naz AI Bot в голос VOID и публикую.")
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


@router.message(Command("auto_on"))
async def auto_on_command(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    set_setting("auto_publish", "1")
    await message.answer(
        "Автопубликация включена.\n"
        "VOID будет сам искать, проверять и публиковать лучший сигнал примерно раз в 3 часа.\n"
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
    await message.answer(f"Автопубликация: {'включена' if enabled else 'выключена'}")


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


@router.message()
async def free_text_handler(message: Message):

    chat_id = message.chat.id
    user_id = message.from_user.id
    text = (message.text or "").strip()

    if await handle_reply_button(message, text):
        return

    session = get_dialog_session(user_id)

    history = get_dialog_context(user_id, limit=8)
    personality = session.get("personality", "observer")

    if not text:
        return

    memory_note = get_dialog_memory(user_id)
    prompt = build_dialog_prompt(text, personality, history, memory_note)

    try:
        reply = await asyncio.to_thread(
            call_ai,
            prompt,
            text,
            model=OPENAI_DIALOG_MODEL,
        )
        reply = (reply or "").strip() or "Я не успел сформулировать ответ. Попробуй ещё раз."
    except Exception as e:
        print("DIALOG AI ERROR:", repr(e))
        reply = f"AI ERROR: {e}"

    save_dialog_message(user_id, "user", text)
    save_dialog_message(user_id, "assistant", reply)

    new_memory = build_memory_note(text, reply)
    if new_memory:
        save_dialog_message(user_id, "memory", new_memory)

    await message.answer(reply, reply_markup=reply_main_keyboard())


async def run_bot_once():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

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
