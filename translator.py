from __future__ import annotations

import json
import re
from typing import Any

from config import settings

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")


TRANSLATOR_INSTRUCTIONS = """
Ты — переводчик и выпускающий редактор VOID.

Задача: перевести новости на русский для Telegram-бота.
Не добавляй мнения. Не превращай заголовок в кликбейт. Не выдумывай детали.
Сохрани смысл, источник и факты.

Стиль русского:
- естественный
- короткий
- без канцелярита
- без машинного перевода
- технологические термины можно оставлять как AI, LLM, startup, database, если так звучит лучше

Верни строго JSON того же формата:
{
  "items": [
    {"id": 1, "title_ru": "...", "summary_ru": "..."}
  ]
}
""".strip()


def looks_russian(text: str | None) -> bool:
    return bool(text and _CYRILLIC_RE.search(text))


def _extract_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def translate_news_items_to_ru(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate scan results to Russian when OPENAI_API_KEY is available.

    If no API key exists, returns items unchanged. Draft generation can still run,
    but /scan will show original source titles.
    """
    if not items:
        return items

    if not settings.openai_api_key or OpenAI is None:
        return items

    needs_translation = [
        {
            "id": idx,
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
        }
        for idx, item in enumerate(items)
        if not looks_russian(item.get("title", ""))
    ]

    if not needs_translation:
        return items

    client = OpenAI(api_key=settings.openai_api_key)

    try:
        response = client.responses.create(
            model=settings.openai_model,
            instructions=TRANSLATOR_INSTRUCTIONS,
            input=json.dumps({"items": needs_translation}, ensure_ascii=False),
        )
        data = _extract_json(response.output_text)
        if not data or "items" not in data:
            return items

        translated_by_id: dict[int, dict[str, str]] = {}
        for row in data.get("items", []):
            try:
                translated_by_id[int(row["id"])] = row
            except Exception:
                continue

        result = []
        for idx, item in enumerate(items):
            new_item = dict(item)
            tr = translated_by_id.get(idx)
            if tr:
                title_ru = str(tr.get("title_ru", "")).strip()
                summary_ru = str(tr.get("summary_ru", "")).strip()
                if title_ru:
                    new_item["original_title"] = new_item.get("title", "")
                    new_item["title"] = title_ru
                if summary_ru:
                    new_item["original_summary"] = new_item.get("summary", "")
                    new_item["summary"] = summary_ru
            result.append(new_item)
        return result

    except Exception:
        return items
