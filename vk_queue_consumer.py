"""Standalone deterministic VK queue consumer. Never imports application/LLM code."""
from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Any

from vk_publish_queue import RetryablePublishError, consume_once, requeue_failed

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))
PROFILE_DIR = Path(os.getenv("VK_BROWSER_PROFILE_DIR", "/var/lib/void-vk-publisher/profile"))
KILL_SWITCH = Path(os.getenv("VK_PUBLISH_KILL_SWITCH", "/etc/void-vk-publisher.disabled"))
GROUP_ID = os.getenv("VK_GROUP_ID", "").strip()
COMMUNITY_URL = os.getenv("VK_COMMUNITY_URL", "").strip().rstrip("/")
HEADLESS = os.getenv("VK_BROWSER_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}

CREATE_TEXT = "Создать"
POST_TEXTS = ("Пост", "Запись", "Публикация")
NEXT_TEXT = "Далее"
DONE_TEXT = "Готово"
PUBLISH_TEXT = "Опубликовать"
POST_INPUT_LABEL = "Напишите что-нибудь..."


def allowed_community_url() -> str:
    if not GROUP_ID or not GROUP_ID.isdigit():
        raise RuntimeError("VK_GROUP_ID must contain digits")
    expected = f"https://vk.com/club{GROUP_ID}"
    if COMMUNITY_URL != expected:
        raise RuntimeError("VK_COMMUNITY_URL does not match the single allowed community")
    return expected


def _click_first_text(page: Any, labels: tuple[str, ...], timeout: int = 15_000) -> None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for label in labels:
            locator = page.get_by_text(label, exact=True)
            if locator.count() and locator.first.is_visible():
                try:
                    # VK may keep the click navigation request open even though
                    # the composer changed state, or replace the button between
                    # lookup and click. Neither should fail the queue job.
                    locator.first.click(timeout=3_000, force=True, no_wait_after=True)
                    return
                except Exception as exc:
                    if type(exc).__name__ != "TimeoutError":
                        raise
                    continue
        page.wait_for_timeout(250)
    raise RuntimeError("required VK composer control not found")


def _open_composer(page: Any) -> None:
    button = page.locator('[data-testid="group_publish_block"] button').first
    if button.count() and button.is_visible():
        button.click(timeout=15_000, force=True, no_wait_after=True)
    else:
        # Admin cards can push the lazy-rendered composer below the initial
        # viewport. Scroll to the feed before falling back to the text button.
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1_500)
        _click_first_text(page, (CREATE_TEXT,))
    page.wait_for_timeout(700)
    _click_first_text(page, POST_TEXTS)
    page.wait_for_timeout(1_200)


def _post_input(page: Any) -> Any:
    for locator in (page.locator(f'[aria-label="{POST_INPUT_LABEL}"]'), page.locator('[contenteditable="true"][role="textbox"]'), page.locator('[contenteditable="true"]')):
        if locator.count() and locator.last.is_visible():
            return locator.last
    raise RuntimeError("VK post editor input not found")


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", value.casefold()) if len(part) >= 3}


def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
    return None


def _audio_search_input(page: Any, timeout: int = 10_000) -> Any | None:
    selectors = (
        '[data-testid="posting_audio_search_audio_input"]',
        '[role="dialog"] input[data-testid*="audio"][data-testid*="search"]',
        '[role="dialog"] input[type="search"]',
        '[role="dialog"] input[placeholder*="Поиск"]',
        'input[data-testid*="audio"][data-testid*="search"]',
    )
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        candidate = _first_visible(page, selectors)
        if candidate is not None:
            return candidate
        page.wait_for_timeout(250)
    return None


def _open_audio_picker(page: Any) -> Any:
    existing = _audio_search_input(page, timeout=500)
    if existing is not None:
        return existing

    for label in ("Музыка", "Аудиозапись", "Аудио"):
        locator = page.get_by_text(label, exact=True)
        if locator.count() and locator.last.is_visible():
            locator.last.click(timeout=5_000, force=True, no_wait_after=True)
            page.wait_for_timeout(1_000)
            search = _audio_search_input(page, timeout=5_000)
            if search is not None:
                return search

    trigger = _first_visible(
        page,
        (
            'button[data-testid*="audio"]',
            '[role="button"][data-testid*="audio"]',
            'button[aria-label*="аудио"]',
            '[role="button"][aria-label*="аудио"]',
            'button[title*="аудио"]',
        ),
    )
    if trigger is not None:
        trigger.click(timeout=5_000, force=True, no_wait_after=True)
        page.wait_for_timeout(1_000)
        search = _audio_search_input(page, timeout=5_000)
        if search is not None:
            return search

    # Last-resort compatibility with the previous fixed-layout VK composer.
    page.mouse.click(525, 536)
    page.wait_for_timeout(1_500)
    search = _audio_search_input(page, timeout=10_000)
    if search is None:
        raise RetryablePublishError("VK audio search input is unavailable; retry later")
    return search


def _attach_track(page: Any, query: str) -> None:
    if not query:
        raise RetryablePublishError("VK track query is missing")
    search = _open_audio_picker(page)
    try:
        search.fill(query, timeout=10_000)
    except Exception as exc:
        raise RetryablePublishError("VK audio search input is not ready; retry later") from exc
    page.wait_for_timeout(5_000)
    rows = page.locator(
        '[data-testid="posting_audio_audio_track_row"], '
        '[data-testid*="audio_track_row"]'
    )
    query_tokens = _tokens(query)
    scored: list[tuple[int, int]] = []
    for index in range(min(rows.count(), 30)):
        score = len(query_tokens & _tokens(rows.nth(index).inner_text(timeout=2_000)))
        scored.append((score, index))
    required_score = max(1, min(2, len(query_tokens)))
    if not scored or max(scored)[0] < required_score:
        raise RetryablePublishError("no matching VK audio result; retry later")
    rows.nth(max(scored)[1]).click(timeout=10_000)
    page.wait_for_timeout(800)
    page.get_by_text(DONE_TEXT, exact=True).last.click(timeout=10_000)
    page.wait_for_timeout(1_500)


def publish_job(job: dict[str, Any], media: list[Path]) -> None:
    from playwright.sync_api import sync_playwright

    url = allowed_community_url()
    if not PROFILE_DIR.is_dir() or PROFILE_DIR.is_symlink():
        raise RuntimeError("authorized browser profile is unavailable")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(user_data_dir=str(PROFILE_DIR), headless=HEADLESS, viewport={"width": 1400, "height": 900})
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(2_500)
            _open_composer(page)
            if media:
                page.locator("input[type=file]").last.set_input_files([str(path) for path in media])
                page.wait_for_timeout(4_000)
            _post_input(page).fill(job["text"])
            page.wait_for_timeout(700)
            _click_first_text(page, (NEXT_TEXT,))
            page.wait_for_timeout(2_500)
            _attach_track(page, job["track_query"])
            page.get_by_text(PUBLISH_TEXT, exact=True).last.click(timeout=15_000)
            page.wait_for_timeout(6_000)
        finally:
            context.close()


def consume_queue() -> int:
    if KILL_SWITCH.exists():
        print("VK publisher disabled by kill switch")
        return 75
    allowed_community_url()
    return consume_once(QUEUE_DIR, GROUP_ID, publish_job)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone allowlisted VK queue consumer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("consume-queue")
    requeue = sub.add_parser("requeue-failed")
    requeue.add_argument("job_id")
    args = parser.parse_args()
    if args.command == "consume-queue":
        raise SystemExit(consume_queue())
    path = requeue_failed(QUEUE_DIR, args.job_id, GROUP_ID)
    print(f"Requeued: {path}")


if __name__ == "__main__":
    main()
