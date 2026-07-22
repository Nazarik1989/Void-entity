"""Standalone deterministic VK queue consumer. Never imports application/LLM code."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from vk_publish_queue import RetryablePublishError, consume_once, requeue_failed

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))
PROFILE_DIR = Path(os.getenv("VK_BROWSER_PROFILE_DIR", "/var/lib/void-vk-publisher/profile"))
KILL_SWITCH = Path(os.getenv("VK_PUBLISH_KILL_SWITCH", "/etc/void-vk-publisher.disabled"))
GROUP_ID = os.getenv("VK_GROUP_ID", "").strip()
COMMUNITY_URL = os.getenv("VK_COMMUNITY_URL", "").strip().rstrip("/")
HEADLESS = os.getenv("VK_BROWSER_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_NOTICES_DIRNAME = "admin-notices"
ADMIN_NOTICE_SCHEMA = "vk_admin_notice.v1"

CREATE_TEXT = "Создать"
POST_TEXTS = ("Пост", "Запись", "Публикация")
NEXT_TEXT = "Далее"
DONE_TEXT = "Готово"
PUBLISH_TEXT = "Опубликовать"
POST_INPUT_LABEL = "Напишите что-нибудь..."

COMPOSER_TRIGGER_SELECTORS = (
    '[data-testid="group_publish_block"] button',
    '[data-testid="group_publish_block"] [role="button"]',
    '[data-testid*="group_publish"] [role="button"]',
    '[role="button"][aria-label*="Создать запись"]',
    '[role="button"][aria-label*="Создать публикацию"]',
    '[data-testid*="composer"] [role="button"]',
)
COMPOSER_INPUT_SELECTORS = (
    f'[aria-label="{POST_INPUT_LABEL}"]',
    '[data-testid*="composer"] [contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    '[role="textbox"][aria-label*="запис"]',
    '[role="textbox"][aria-label*="публикац"]',
)
AUTH_SELECTORS = (
    'form[action^="/login"]',
    'form[action^="https://login.vk.com/"]',
    'input[name="email"][autocomplete="username"]',
    'input[name="login"][autocomplete="username"]',
    'input[type="password"][name="pass"]',
    '[data-testid="login_form"]',
    '[data-testid="login_button"]',
)


class VkAuthenticationRequiredError(RuntimeError):
    """The persistent browser profile needs explicit administrator attention."""


class VkComposerStructureError(RuntimeError):
    """VK returned an unknown composer structure; retrying blindly is unsafe."""


def allowed_community_url() -> str:
    if not GROUP_ID or not GROUP_ID.isdigit():
        raise RuntimeError("VK_GROUP_ID must contain digits")
    expected = f"https://vk.com/club{GROUP_ID}"
    if COMMUNITY_URL != expected:
        raise RuntimeError("VK_COMMUNITY_URL does not match the single allowed community")
    return expected


def _record_admin_notice(job_id: str, code: str) -> Path:
    """Write a metadata-only notice; never include profile or credential data."""
    notices = QUEUE_DIR / ADMIN_NOTICES_DIRNAME
    if notices.is_symlink():
        raise RuntimeError("VK admin notice directory is unsafe")
    notices.mkdir(mode=0o770, parents=True, exist_ok=True)
    final = notices / f"{job_id}.json"
    temp = notices / f".{job_id}.tmp-{uuid.uuid4().hex}"
    payload = {
        "schema": ADMIN_NOTICE_SCHEMA,
        "job_id": str(job_id),
        "code": str(code),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    temp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temp, 0o640)
    os.replace(temp, final)
    print(f"VK publisher admin notice: {code} job_id={job_id}", flush=True)
    return final


def _click_first_text(
    page: Any,
    labels: tuple[str, ...],
    timeout: int = 15_000,
    *,
    missing_error: type[Exception] = VkComposerStructureError,
) -> None:
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
                        raise VkComposerStructureError("VK composer control failed structurally") from exc
                    continue
        page.wait_for_timeout(250)
    raise missing_error("required VK composer control not found")


def _authentication_required(page: Any) -> bool:
    current_url = str(getattr(page, "url", "") or "")
    parsed = urlparse(current_url)
    path = parsed.path.rstrip("/").casefold()
    query = parse_qs(parsed.query)
    if (
        parsed.hostname == "login.vk.com"
        or path == "/login"
        or path.startswith("/login/")
        or any(value.casefold() == "login" for value in query.get("act", ()))
    ):
        return True
    return _first_visible(page, AUTH_SELECTORS) is not None


def _composer_input(page: Any) -> Any | None:
    return _first_visible(page, COMPOSER_INPUT_SELECTORS)


def _open_composer_once(page: Any) -> None:
    if _authentication_required(page):
        raise VkAuthenticationRequiredError("VK browser session authentication is required")
    if _composer_input(page) is not None:
        return

    trigger = _first_visible(page, COMPOSER_TRIGGER_SELECTORS)
    if trigger is None:
        # Admin cards can push the lazy-rendered composer below the viewport.
        page.mouse.wheel(0, 900)
        page.wait_for_timeout(1_500)
        trigger = _first_visible(page, COMPOSER_TRIGGER_SELECTORS)
    try:
        if trigger is not None:
            trigger.click(timeout=5_000, force=True, no_wait_after=True)
        else:
            _click_first_text(
                page,
                (CREATE_TEXT,),
                timeout=3_000,
                missing_error=RetryablePublishError,
            )
    except RetryablePublishError:
        raise
    except VkComposerStructureError:
        raise
    except Exception as exc:
        if type(exc).__name__ == "TimeoutError":
            raise RetryablePublishError("VK composer trigger is temporarily unavailable") from exc
        raise VkComposerStructureError("VK composer trigger failed structurally") from exc

    page.wait_for_timeout(700)
    if _authentication_required(page):
        raise VkAuthenticationRequiredError("VK browser session authentication is required")
    if _composer_input(page) is not None:
        return
    _click_first_text(
        page,
        POST_TEXTS,
        timeout=3_000,
        missing_error=RetryablePublishError,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if _authentication_required(page):
            raise VkAuthenticationRequiredError("VK browser session authentication is required")
        if _composer_input(page) is not None:
            return
        page.wait_for_timeout(250)
    raise RetryablePublishError("VK composer is temporarily unavailable")


def _open_composer(page: Any) -> None:
    """Open composer with one bounded safe reload, never a publish action."""
    try:
        _open_composer_once(page)
        return
    except VkAuthenticationRequiredError:
        raise
    except RetryablePublishError:
        try:
            page.reload(wait_until="domcontentloaded", timeout=15_000)
            page.wait_for_timeout(1_500)
        except Exception as exc:
            raise RetryablePublishError("VK community reload failed; retry later") from exc
        try:
            _open_composer_once(page)
            return
        except VkAuthenticationRequiredError:
            raise
        except RetryablePublishError as exc:
            raise RetryablePublishError("VK composer is temporarily unavailable; retry later") from exc


def _post_input(page: Any) -> Any:
    candidate = _composer_input(page)
    if candidate is not None:
        return candidate
    raise VkComposerStructureError("VK post editor input not found")


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
            try:
                _open_composer(page)
            except VkAuthenticationRequiredError:
                _record_admin_notice(job["job_id"], "vk_session_authentication_required")
                raise
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
