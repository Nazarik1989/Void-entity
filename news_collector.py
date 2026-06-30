import html
import re
from typing import Any

import feedparser

from config import settings
from sources import RSS_SOURCES
from void_lens import analyze_news_item


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def clean_html(value: str | None) -> str:
    if not value:
        return ""
    value = html.unescape(value)
    value = _TAG_RE.sub(" ", value)
    value = _SPACE_RE.sub(" ", value)
    return value.strip()


def collect_rss_news(limit_per_source: int | None = None) -> list[dict[str, Any]]:
    limit = limit_per_source or settings.scan_limit_per_source
    items: list[dict[str, Any]] = []

    for source in RSS_SOURCES:
        feed = feedparser.parse(source["url"])
        for entry in feed.entries[:limit]:
            title = clean_html(entry.get("title", ""))
            summary = clean_html(entry.get("summary", ""))
            url = entry.get("link", "")
            published_at = entry.get("published", "") or entry.get("updated", "")

            if not title or not url:
                continue

            item = {
                "title": title,
                "summary": summary[:900],
                "url": url,
                "source_name": source["name"],
                "source_bias": source.get("bias", ""),
                "published_at": published_at,
            }
            item.update(analyze_news_item(title, summary))
            items.append(item)

    seen: set[str] = set()
    unique_items: list[dict[str, Any]] = []
    for item in items:
        if item["url"] in seen:
            continue
        seen.add(item["url"])
        unique_items.append(item)

    return sorted(unique_items, key=lambda x: x.get("score", 0), reverse=True)
