
from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import feedparser
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import load_dotenv

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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

DB_PATH = "void.db"

router = Router()
auto_task: asyncio.Task | None = None


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

MODE_RUBRICS = {
    "news": "SIGNAL",
    "manual": "SIGNAL",
    "midnight": "MIDNIGHT",
    "observation": "OBSERVATION",
    "future": "FUTURE FILE",
    "digest": "VOID DIGEST",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
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

            items.append(
                {
                    "title": title,
                    "summary": summary,
                    "url": url,
                    "source_name": source["name"],
                    "frequency": frequency,
                    "score": score,
                }
            )

    items.sort(key=lambda x: x["score"], reverse=True)
    return items


def openai_client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK не установлен")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY не задан")

    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    if OPENAI_BASE_URL:
        kwargs["base_url"] = OPENAI_BASE_URL

    return OpenAI(**kwargs)


def call_ai(instructions: str, input_text: str) -> str:
    client = openai_client()
    response = client.responses.create(
        model=OPENAI_MODEL,
        instructions=instructions,
        input=input_text,
    )
    return response.output_text.strip()


def too_much_english(text: str) -> bool:
    words = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", text or "")
    allowed = {"void", "ai", "llm", "api", "rss", "openai", "url", "mit", "wired"}
    bad = [w for w in words if w.lower() not in allowed]
    return len(bad) >= 8


def quality_check(post: str) -> tuple[bool, str]:
    if len(post.strip()) < 250:
        return False, "слишком коротко"
    if len(post) > 1800:
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
        "future": "Сделай FUTURE FILE: чуть аналитичнее, про будущий сдвиг, но человеческим языком.",
        "digest": "Сделай дайджест дня: 3–5 сигналов, общий вывод, немного иронии.",
    }

    return f"""
Ты — редактор Telegram-канала VOID.

Пиши СТРОГО НА РУССКОМ.
Голос VOID: умный, живой, наблюдательный, с сухим юмором.
НЕ душни. Не делай вид, что каждое обновление интерфейса — падение Рима.
Юмор нужен обязательно: 1–2 коротких ироничных укола, но без клоунады.
Можно материться? Нет. Но можно звучать так, будто очень хочется.

Рубрика: {rubric}
Частота: {frequency}

Задача режима:
{mode_rules.get(mode, mode_rules["manual"])}

Запрещено:
- "в современном мире"
- "будущее уже наступило"
- "технологии меняют нашу жизнь"
- мотивационные статусы
- эзотерика
- подростковый мемный тон
- высокомерие к людям

Структура:
1. Заголовок рубрики: {rubric} / {frequency} если частота уместна, иначе просто {rubric}
2. Факт / мысль / наблюдение.
3. Что это говорит о человеке в цифровой среде.
4. VOID COMMENT: коротко, иронично, не душно.
5. Источник, если источник есть.

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
        raw = call_ai(prompt, input_text)
        title, post = parse_ai_output(raw)

        if too_much_english(post):
            raw = call_ai(
                prompt + "\n\nПредыдущий вариант оставил слишком много английского. Перепиши полностью по-русски.",
                input_text,
            )
            title, post = parse_ai_output(raw)

        if source_url and source_url.startswith("http") and "Источник:" not in post:
            post = f"{post.rstrip()}\n\nИсточник: {source_name}\n{source_url}"

    except Exception as e:
        title, post = fallback_post(mode, content, source_name, source_url, frequency)
        post += f"\n\nDIAG: {type(e).__name__}: {e}"

    return {
        "mode": mode,
        "title": title,
        "post": post[:1800],
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
        disable_web_page_preview=False,
    )
    mark_published(draft_id, draft["source_url"] or "")
    return f"Опубликовано: #{draft_id}"


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
            "news",
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
            "news",
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


async def auto_loop(bot: Bot) -> None:
    while True:
        try:
            enabled = get_setting("auto_publish", "0") == "1"
            if enabled:
                result = await autopost_once(bot)
                print(result, flush=True)
        except Exception as e:
            print(f"auto_loop error: {type(e).__name__}: {e}", flush=True)

        await asyncio.sleep(60 * 60 * 3)


@router.message(CommandStart())
async def start(message: Message):
    if not is_admin(message):
        await message.answer("VOID online. Публичный режим будет позже.")
        return

    await message.answer(
        "VOID ONLINE.\n\n"
        "/news — найти новости и сделать черновики\n"
        "/manual твоя мысль — сигнал из мысли\n"
        "/midnight текст — ночной сигнал\n"
        "/observation текст — наблюдение\n"
        "/future текст — future file\n"
        "/digest — дайджест дня\n\n"
        "/drafts — черновики\n"
        "/preview ID — посмотреть\n"
        "/publish ID — опубликовать\n"
        "/auto_on — сам ищет, проверяет и публикует\n"
        "/auto_off — выключить автопубликацию"
    )


@router.message(Command("help"))
async def help_command(message: Message):
    await start(message)


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


@router.message(Command("midnight"))
async def midnight_command(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Используй: /midnight ночная мысль")
        return
    await manual_like(message, "midnight", parts[1])


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


@router.message(Command("autopost_now"))
async def autopost_now_command(message: Message, bot: Bot):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    await message.answer("Запускаю автопостинг один раз. VOID надевает редакторские перчатки.")
    result = await autopost_once(bot)
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
    conn.close()
    await callback.answer("Поймал.")


@router.message(F.text)
async def free_text_handler(message: Message):
    if not is_admin(message):
        await message.answer(admin_required())
        return

    text = (message.text or "").strip()
    if text.startswith("/"):
        await message.answer("Команда не распознана. /help — список команд.")
        return

    await manual_like(message, "manual", text)


async def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")

    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    global auto_task
    auto_task = asyncio.create_task(auto_loop(bot))

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
