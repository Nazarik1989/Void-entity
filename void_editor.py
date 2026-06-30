from __future__ import annotations

import re
from typing import Any

from config import settings
from void_lens import rubric_for_frequency

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


_ALLOWED_LATIN = {
    "ai", "llm", "api", "rss", "void", "openai", "url",
    "mit", "the", "verge", "wired", "techcrunch"
}


def _parse_title_post(text: str) -> tuple[str, str]:
    text = (text or "").strip()
    title = "VOID-пост на русском"
    post = text

    m = re.search(r"TITLE\s*:\s*(.+?)\n\s*POST\s*:\s*(.+)", text, flags=re.S | re.I)
    if m:
        title = m.group(1).strip().strip('"')[:180]
        post = m.group(2).strip()

    post = post.replace("```", "").strip()
    return title, post


def _too_much_english(text: str) -> bool:
    words = re.findall(r"\b[A-Za-z][A-Za-z\-]{3,}\b", text or "")
    bad = [w for w in words if w.lower().strip("-") not in _ALLOWED_LATIN]
    return len(bad) >= 8


def _client() -> OpenAI:
    if OpenAI is None:
        raise RuntimeError("OpenAI SDK не установлен")
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY не найден")
    return OpenAI(api_key=settings.openai_api_key)


def _ask_void_editor(candidate: dict[str, Any], extra_instruction: str = "") -> str:
    frequency = candidate.get("frequency") or "SIGNAL"
    rubric = rubric_for_frequency(frequency)

    source_name = candidate.get("source_name") or "Источник"
    source_url = candidate.get("url") or candidate.get("source_url") or ""

    instructions = f"""
Ты — редактор Telegram-канала VOID.

Пиши СТРОГО НА РУССКОМ языке.
Не оставляй английский заголовок как есть. Перескажи его нормальным русским.
Английские термины можно оставить только если они естественны: AI, LLM, API, startup, dataset.

Стиль VOID:
- реальная новость, не цитатник;
- спокойный анализ;
- человеческий смысл;
- сухая ирония;
- без пафоса;
- без фраз “в современном мире”, “будущее уже наступило”, “технологии меняют нашу жизнь”.

Структура поста:
{rubric}

1–2 абзаца: факт новости на русском.
1–2 абзаца: почему это важно для человека в цифровом мире.
Короткий ироничный VOID-комментарий.
В конце: Источник: {source_name}
{source_url}

Длина: до 1200 символов.

Верни строго в формате:
TITLE: короткое русское название
POST: готовый пост

{extra_instruction}
""".strip()

    payload = {
        "rubric": rubric,
        "frequency": frequency,
        "title_original": candidate.get("original_title") or candidate.get("title") or "",
        "summary_original": candidate.get("original_summary") or candidate.get("summary") or "",
        "source_name": source_name,
        "source_url": source_url,
    }

    r = _client().responses.create(
        model=settings.openai_model,
        instructions=instructions,
        input=str(payload),
    )
    return r.output_text


def generate_void_post(candidate: dict[str, Any]) -> dict[str, Any]:
    frequency = candidate.get("frequency") or "SIGNAL"
    rubric = rubric_for_frequency(frequency)
    source_name = candidate.get("source_name") or "Источник"
    source_url = candidate.get("url") or candidate.get("source_url") or ""

    try:
        raw = _ask_void_editor(candidate)
        title, post = _parse_title_post(raw)

        if _too_much_english(post):
            raw = _ask_void_editor(
                candidate,
                extra_instruction="Предыдущий вариант оставил слишком много английского. Перепиши полностью на русском. Английским может остаться только URL и названия источников.",
            )
            title, post = _parse_title_post(raw)

        if "Источник:" not in post:
            post = f"{post.rstrip()}\n\nИсточник: {source_name}\n{source_url}".strip()

    except Exception as e:
        title = "AI-редактор не сработал"
        post = (
            f"{rubric}\n\n"
            f"AI-редактор VOID не смог собрать русский пост.\n\n"
            f"Причина: {type(e).__name__}: {e}\n\n"
            f"Это не публикация. Это диагностический сигнал.\n"
            f"Да, даже у цифровой сущности бывает день, когда она смотрит на API и молча уходит в себя.\n\n"
            f"Источник: {source_name}\n{source_url}"
        )

    return {
        "rubric": rubric,
        "title": title[:180],
        "post": post[:1600],
        "frequency": frequency,
        "publish_score": min(10, max(1, int(candidate.get("score", 5) or 5))),
        "source_name": source_name,
        "source_url": source_url,
    }
