"""Standalone deterministic VK queue consumer. Never imports application/LLM code."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from vk_publish_queue import (
    RetryablePublishError,
    consume_once,
    publication_receipts,
    requeue_failed,
)

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))
PROFILE_DIR = Path(os.getenv("VK_BROWSER_PROFILE_DIR", "/var/lib/void-vk-publisher/profile"))
KILL_SWITCH = Path(os.getenv("VK_PUBLISH_KILL_SWITCH", "/etc/void-vk-publisher.disabled"))
GROUP_ID = os.getenv("VK_GROUP_ID", "").strip()
COMMUNITY_URL = os.getenv("VK_COMMUNITY_URL", "").strip().rstrip("/")
HEADLESS = os.getenv("VK_BROWSER_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
ADMIN_NOTICES_DIRNAME = "admin-notices"
ADMIN_NOTICE_SCHEMA = "vk_admin_notice.v1"
PUBLICATION_ATTEMPT_FILENAME = ".publication-attempt-unresolved.json"
PUBLICATION_ATTEMPT_SCHEMA = "vk_publication_attempt.v1"

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
AUDIO_SEARCH_SELECTORS = (
    '[data-testid="posting_audio_search_audio_input"]',
    '[role="dialog"] input[data-testid*="audio"][data-testid*="search"]',
    '[role="dialog"] input[type="search"]',
    '[role="dialog"] input[placeholder*="Поиск"]',
    'input[data-testid*="audio"][data-testid*="search"]',
)
ATTACHED_AUDIO_SELECTORS = (
    '[data-testid="posting_audio_audio_track_row"]',
    '[data-testid*="audio_track_row"]',
    '[data-testid*="audio"][data-testid*="track"]',
    '[class*="audio_row"]',
    '[class*="AudioRow"]',
    'a[href*="/audio"]',
)
PUBLISHED_POST_SELECTORS = (
    '[data-post-id]',
    '[data-post_id]',
    '[data-testid="post"]',
    '[data-testid*="feed_item"]',
    'article[id^="post"]',
    '[id^="post-"]',
    '[id^="post_"]',
)
PUBLISHED_AUDIO_SELECTORS = (
    '[data-testid*="audio"]',
    '[class*="audio_row"]',
    '[class*="AudioRow"]',
    'a[href*="/audio"]',
)
AUDIO_VARIANT_TOKENS = frozenset(
    {
        "acoustic",
        "bootleg",
        "cover",
        "demo",
        "edit",
        "extended",
        "instrumental",
        "karaoke",
        "live",
        "mix",
        "radio",
        "remix",
        "rework",
        "slowed",
        "sped",
        "version",
        "акустика",
        "версия",
        "инструментал",
        "кавер",
        "лайв",
        "ремикс",
    }
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


class VkPublishConfirmationError(RuntimeError):
    """A publish was attempted but its exact browser-visible result is unproven."""


@dataclass(frozen=True)
class _PublicationEvidence:
    identified: frozenset[str]
    anonymous_count: int
    observed_identified: frozenset[str] = frozenset()


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


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _publication_attempt_path() -> Path:
    return QUEUE_DIR / PUBLICATION_ATTEMPT_FILENAME


def _load_publication_attempt() -> dict[str, str] | None:
    path = _publication_attempt_path()
    if path.is_symlink():
        raise RuntimeError("VK unresolved publication marker is unsafe")
    if not path.exists():
        return None
    if not path.is_file():
        raise RuntimeError("VK unresolved publication marker is invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("VK unresolved publication marker is invalid") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "job_id", "created_at"}
        or payload.get("schema") != PUBLICATION_ATTEMPT_SCHEMA
        or not isinstance(payload.get("job_id"), str)
        or not payload["job_id"]
        or not isinstance(payload.get("created_at"), str)
    ):
        raise RuntimeError("VK unresolved publication marker is invalid")
    try:
        created_at = datetime.fromisoformat(payload["created_at"])
    except ValueError as exc:
        raise RuntimeError("VK unresolved publication marker is invalid") from exc
    if created_at.tzinfo is None:
        raise RuntimeError("VK unresolved publication marker is invalid")
    return {key: str(payload[key]) for key in payload}


def _record_publication_attempt(job_id: str) -> Path:
    if QUEUE_DIR.is_symlink() or not QUEUE_DIR.is_dir():
        raise RuntimeError("VK queue directory is unavailable")
    final = _publication_attempt_path()
    if final.exists() or final.is_symlink():
        raise VkPublishConfirmationError(
            "another VK publication attempt is unresolved; manual confirmation is required"
        )
    temp = QUEUE_DIR / f".{PUBLICATION_ATTEMPT_FILENAME}.tmp-{uuid.uuid4().hex}"
    payload = {
        "schema": PUBLICATION_ATTEMPT_SCHEMA,
        "job_id": str(job_id),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, 0o640)
        os.replace(temp, final)
        _sync_directory(QUEUE_DIR)
    finally:
        if temp.exists() and not temp.is_symlink():
            temp.unlink()
    return final


def _unresolved_publication_attempt() -> bool:
    attempt = _load_publication_attempt()
    if attempt is None:
        return False
    confirmed_ids = {
        receipt["job_id"] for receipt in publication_receipts(QUEUE_DIR)
    }
    if attempt["job_id"] not in confirmed_ids:
        return True
    marker = _publication_attempt_path()
    marker.unlink()
    _sync_directory(QUEUE_DIR)
    return False


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
    return {
        part
        for part in re.split(r"[^0-9A-Za-zА-Яа-яЁё]+", value.casefold())
        if part
    }


def _audio_identity_matches(value: str, query: str) -> bool:
    """Match the requested catalog identity without silently changing versions."""
    query_tokens = _tokens(query)
    value_tokens = _tokens(value)
    if not query_tokens or not query_tokens <= value_tokens:
        return False
    requested_variants = query_tokens & AUDIO_VARIANT_TOKENS
    displayed_variants = value_tokens & AUDIO_VARIANT_TOKENS
    return displayed_variants <= requested_variants


def _normalized_visible_text(value: str) -> str:
    without_zero_width = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
    return " ".join(without_zero_width.split()).casefold()


def _locator_search_text(locator: Any) -> str:
    values: list[str] = []
    for method_name in ("inner_text", "text_content"):
        try:
            value = getattr(locator, method_name)(timeout=2_000)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            values.append(value)
    for attribute in ("aria-label", "title"):
        try:
            value = locator.get_attribute(attribute)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            values.append(value)
    return " ".join(dict.fromkeys(values))


def _visible_matching_audio_count(page: Any, query: str) -> int:
    if not _tokens(query):
        return 0
    matches = 0
    locator = page.locator(", ".join(ATTACHED_AUDIO_SELECTORS))
    count = min(locator.count(), 60)
    for index in range(count):
        candidate = locator.nth(index)
        try:
            visible = candidate.is_visible()
        except Exception:
            continue
        if visible and _audio_identity_matches(
            _locator_search_text(candidate), query
        ):
            matches += 1
    return matches


def _confirm_track_attached(
    page: Any,
    query: str,
    previous_match_count: int,
    timeout: int = 10_000,
) -> None:
    deadline = time.monotonic() + timeout / 1000
    while True:
        if _authentication_required(page):
            raise VkAuthenticationRequiredError(
                "VK browser session authentication is required"
            )
        picker_closed = _first_visible(page, AUDIO_SEARCH_SELECTORS) is None
        try:
            match_count = _visible_matching_audio_count(page, query)
        except Exception:
            match_count = previous_match_count
        if picker_closed and match_count > previous_match_count:
            return
        if time.monotonic() >= deadline:
            break
        page.wait_for_timeout(250)
    raise RetryablePublishError(
        "VK did not confirm the requested audio attachment; retry later"
    )


def _post_identifier(post: Any) -> str | None:
    for attribute in ("data-post-id", "data-post_id", "id"):
        try:
            value = post.get_attribute(attribute)
        except Exception:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        wall_id = re.search(r"-?\d+_\d+", value)
        if wall_id:
            return f"wall:{wall_id.group(0)}"
        return f"{attribute}:{value.strip()}"
    try:
        links = post.locator('a[href*="wall"]')
        for index in range(min(links.count(), 20)):
            href = links.nth(index).get_attribute("href")
            if not isinstance(href, str):
                continue
            wall_id = re.search(r"wall(-?\d+_\d+)", href)
            if wall_id:
                return f"wall:{wall_id.group(1)}"
    except Exception:
        pass
    return None


def _post_has_matching_audio(post: Any, query: str) -> bool:
    try:
        audio = post.locator(", ".join(PUBLISHED_AUDIO_SELECTORS))
        count = min(audio.count(), 60)
    except Exception:
        return False
    for index in range(count):
        candidate = audio.nth(index)
        try:
            visible = candidate.is_visible()
        except Exception:
            continue
        if visible and _audio_identity_matches(
            _locator_search_text(candidate), query
        ):
            return True
    return False


def _published_post_evidence(
    page: Any,
    text: str,
    track_query: str,
) -> _PublicationEvidence:
    expected_text = _normalized_visible_text(text)
    query_tokens = _tokens(track_query)
    if not expected_text or not query_tokens:
        return _PublicationEvidence(frozenset(), 0)
    identified: set[str] = set()
    observed_identified: set[str] = set()
    anonymous_count = 0
    posts = page.locator(", ".join(PUBLISHED_POST_SELECTORS))
    count = min(posts.count(), 60)
    for index in range(count):
        post = posts.nth(index)
        try:
            visible = post.is_visible()
        except Exception:
            continue
        if not visible:
            continue
        identifier = _post_identifier(post)
        if identifier is not None:
            observed_identified.add(identifier)
        post_text = _normalized_visible_text(_locator_search_text(post))
        if expected_text not in post_text or not _post_has_matching_audio(
            post, track_query
        ):
            continue
        if identifier is None:
            anonymous_count += 1
        else:
            identified.add(identifier)
    return _PublicationEvidence(
        frozenset(identified),
        anonymous_count,
        frozenset(observed_identified),
    )


def _wait_for_publication_confirmation(
    page: Any,
    text: str,
    track_query: str,
    before: _PublicationEvidence,
    timeout: int = 30_000,
) -> None:
    deadline = time.monotonic() + timeout / 1000
    reload_at = time.monotonic() + min(10, timeout / 2000)
    reloaded = False
    while True:
        if _authentication_required(page):
            raise VkPublishConfirmationError(
                "VK requested authentication after publish; outcome is unknown"
            )
        try:
            current = _published_post_evidence(page, text, track_query)
        except Exception:
            current = _PublicationEvidence(frozenset(), 0)
        before_ids = before.observed_identified or before.identified
        if current.identified - before_ids:
            return
        now = time.monotonic()
        if now >= deadline:
            break
        if not reloaded and now >= reload_at:
            try:
                page.reload(wait_until="domcontentloaded", timeout=15_000)
                page.wait_for_timeout(1_500)
            except Exception:
                pass
            reloaded = True
        else:
            page.wait_for_timeout(500)
    raise VkPublishConfirmationError(
        "VK publish was attempted, but no new post with the requested audio was confirmed"
    )


def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
    return None


def _audio_search_input(page: Any, timeout: int = 10_000) -> Any | None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        candidate = _first_visible(page, AUDIO_SEARCH_SELECTORS)
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
    try:
        previous_match_count = _visible_matching_audio_count(page, query)
    except Exception as exc:
        raise RetryablePublishError(
            "VK audio attachment baseline is unavailable; retry later"
        ) from exc
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
    if not query_tokens:
        raise RetryablePublishError("VK track query has no searchable tokens")
    scored: list[tuple[int, int]] = []
    for index in range(min(rows.count(), 30)):
        row_text = rows.nth(index).inner_text(timeout=2_000)
        row_tokens = _tokens(row_text)
        if _audio_identity_matches(row_text, query):
            scored.append((len(row_tokens - query_tokens), index))
    if not scored:
        raise RetryablePublishError("no matching VK audio result; retry later")
    rows.nth(min(scored)[1]).click(timeout=10_000)
    page.wait_for_timeout(800)
    page.get_by_text(DONE_TEXT, exact=True).last.click(timeout=10_000)
    page.wait_for_timeout(1_500)
    _confirm_track_attached(page, query, previous_match_count)


def _publish_and_confirm(
    page: Any,
    job: dict[str, Any],
    publication_before: _PublicationEvidence,
) -> None:
    publish_controls = page.get_by_text(PUBLISH_TEXT, exact=True)
    if not publish_controls.count() or not publish_controls.last.is_visible():
        raise VkComposerStructureError("VK publish control not found")
    _record_publication_attempt(job["job_id"])
    try:
        publish_controls.last.click(
            timeout=15_000,
            force=True,
            no_wait_after=True,
        )
    except Exception as exc:
        _record_admin_notice(job["job_id"], "vk_publication_confirmation_required")
        raise VkPublishConfirmationError(
            "VK publish click outcome is unknown; manual confirmation is required"
        ) from exc
    try:
        _wait_for_publication_confirmation(
            page,
            job["text"],
            job["track_query"],
            publication_before,
        )
    except VkPublishConfirmationError:
        _record_admin_notice(job["job_id"], "vk_publication_confirmation_required")
        raise


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
                publication_before = _published_post_evidence(
                    page, job["text"], job["track_query"]
                )
            except Exception as exc:
                raise RetryablePublishError(
                    "VK feed baseline is unavailable; retry later"
                ) from exc
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
            _publish_and_confirm(page, job, publication_before)
        finally:
            context.close()


def consume_queue() -> int:
    if KILL_SWITCH.exists():
        print("VK publisher disabled by kill switch")
        return 75
    try:
        if _unresolved_publication_attempt():
            print(
                "VK publisher blocked by an unresolved publication attempt; "
                "manual confirmation is required",
                flush=True,
            )
            return 75
    except Exception as exc:
        print(f"VK publisher cannot validate publication state: {exc}", flush=True)
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
