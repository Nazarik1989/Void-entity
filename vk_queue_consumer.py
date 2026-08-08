"""Standalone deterministic VK queue consumer. Never imports application/LLM code."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
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
    VK_MUSIC_TRACKS_FILE,
    _record_publication_receipt,
    consume_once,
    normalize_track_query,
    publication_receipts,
    requeue_failed,
    validate_job,
)

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))
PROFILE_DIR = Path(os.getenv("VK_BROWSER_PROFILE_DIR", "/var/lib/void-vk-publisher/profile"))
KILL_SWITCH = Path(os.getenv("VK_PUBLISH_KILL_SWITCH", "/etc/void-vk-publisher.disabled"))
GROUP_ID = os.getenv("VK_GROUP_ID", "").strip()
COMMUNITY_URL = os.getenv("VK_COMMUNITY_URL", "").strip().rstrip("/")
HEADLESS = os.getenv("VK_BROWSER_HEADLESS", "true").strip().lower() in {"1", "true", "yes", "on"}
PUBLISH_MIN_INTERVAL_SECONDS = int(
    os.getenv("VK_PUBLISH_MIN_INTERVAL_SECONDS", "0") or "0"
)
if PUBLISH_MIN_INTERVAL_SECONDS < 0:
    raise ValueError("VK_PUBLISH_MIN_INTERVAL_SECONDS must not be negative")
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
    '[data-testid="posting_audio_select_audio_selected"]',
    '[data-testid="posting_audio_select_audio_selected_title"]',
    '[data-testid="posting_preview_attachment_item"]',
    '[data-testid="posting_audio_audio_track_row"]',
    '[data-testid*="audio_track_row"]',
    '[data-testid*="audio"][data-testid*="track"]',
    '[class*="audio_row"]',
    '[class*="AudioRow"]',
    'a[href*="/audio"]',
)
AUDIO_RESULT_SELECTORS = (
    '[role="dialog"] [data-testid="posting_audio_audio_track_row"]',
    '[role="dialog"] [data-testid*="audio"][data-testid*="track"][data-testid*="row"]',
    '[role="dialog"] [data-testid*="audio_row"]',
    '[role="dialog"] [class*="AudioRow"]',
    '[role="dialog"] [class*="audio_row"]',
    '[role="dialog"] [class*="AudioCard"]',
    '[role="dialog"] [class*="audioCard"]',
    '[role="dialog"] [class*="AudioItem"]',
    '[role="dialog"] [class*="audioItem"]',
    '[role="dialog"] [role="option"]',
    '[data-testid="posting_audio_audio_track_row"]',
    '[data-testid*="audio_track_row"]',
    '[class*="AudioCard"]',
    '[class*="audioCard"]',
)
AUDIO_ARTIST_SELECTORS = (
    '[data-testid*="artist"]',
    '[data-testid*="performer"]',
    '[class*="artist"]',
    '[class*="Artist"]',
    '[class*="performer"]',
    '[class*="Performer"]',
)
AUDIO_TITLE_SELECTORS = (
    '[data-testid*="title"]',
    '[data-testid*="name"]',
    '[class*="title"]',
    '[class*="Title"]',
)
AUDIO_PICKER_TRIGGER_SELECTORS = (
    '[data-testid="posting_audio_select_audio_cell"]',
    '[data-testid="group_tab_audios"]',
    'button[data-testid*="audio"]',
    '[role="button"][data-testid*="audio"]',
    '[data-testid*="attach"][data-testid*="audio"]',
    'button[aria-label*="аудио"]',
    '[role="button"][aria-label*="аудио"]',
    'button[title*="аудио"]',
    'button[aria-label*="Музык"]',
    '[role="button"][aria-label*="Музык"]',
    'button[title*="Музык"]',
    'button[aria-label*="музык"]',
    '[role="button"][aria-label*="музык"]',
    'button[title*="музык"]',
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
    '[data-testid*="musicattach"]',
    '[data-testid*="musicoverlaybadge"]',
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
COMPOSER_SCOPE_SELECTORS = (
    '[data-testid="posting_modal_box"]',
    '[role="dialog"][data-testid*="posting"]',
)
COMPOSER_ATTACHMENT_REMOVE_SELECTOR = (
    '[data-testid="posting_attachment_photo_item_remove"], '
    '[data-testid^="posting_attachment_"][data-testid$="_remove"]'
)
COMPOSER_ATTACHMENT_ITEM_SELECTOR = '[data-testid="posting_attachment_item"]'
COMPOSER_DEVICE_UPLOAD_TESTID = "posting_base_screen_download_from_device"
COMPOSER_DEVICE_UPLOAD_SELECTOR = (
    'input[type="file"]'
    f'[data-testid="{COMPOSER_DEVICE_UPLOAD_TESTID}"]'
)
COMPOSER_DEVICE_VIDEO_ACCEPT_TOKENS = frozenset(
    {
        "video/*",
        ".avi",
        ".mp4",
        ".3gp",
        ".mpeg",
        ".mov",
        ".flv",
        ".f4v",
        ".wmv",
        ".mkv",
        ".webm",
        ".vob",
        ".rm",
        ".rmvb",
        ".m4v",
        ".mpg",
        ".ogv",
        ".ts",
        ".m2ts",
        ".mts",
        ".mxf",
        ".quicktime",
    }
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


def _composer_saved_text(editor: Any) -> str:
    for method_name in ("input_value", "inner_text", "text_content"):
        try:
            value = getattr(editor, method_name)(timeout=2_000)
        except Exception:
            continue
        if isinstance(value, str) and value.strip():
            return _normalized_visible_text(value)
    return ""


def _visible_attachment_items(scope: Any, *, limit: int = 60) -> list[Any]:
    try:
        items = scope.locator(COMPOSER_ATTACHMENT_ITEM_SELECTOR)
        count = min(int(items.count()), limit)
    except Exception as exc:
        raise RetryablePublishError(
            "VK composer attachment items are unavailable; retry later"
        ) from exc
    visible: list[Any] = []
    for index in range(count):
        item = items.nth(index)
        try:
            if item.is_visible():
                visible.append(item)
        except Exception as exc:
            raise RetryablePublishError(
                "VK composer attachment item detached; retry later"
            ) from exc
    return visible


def _wait_for_attachment_reduction(
    page: Any,
    scope: Any,
    before: int,
    *,
    timeout: int,
) -> list[Any]:
    deadline = time.monotonic() + max(0, timeout) / 1000
    while True:
        attachments = _visible_attachment_items(scope)
        if len(attachments) < before:
            return attachments
        if time.monotonic() >= deadline:
            raise RetryablePublishError(
                "VK saved composer attachment did not disappear; retry later"
            )
        page.wait_for_timeout(250)


def _managed_saved_draft_texts(job: dict[str, Any]) -> frozenset[str]:
    texts = {_normalized_visible_text(job["text"])}
    for state in ("pending", "processing", "failed"):
        state_root = QUEUE_DIR / state
        if not state_root.is_dir() or state_root.is_symlink():
            continue
        for job_dir in state_root.iterdir():
            if job_dir.is_symlink() or not job_dir.is_dir():
                continue
            try:
                queued = validate_job(job_dir, GROUP_ID)
            except Exception:
                continue
            text = _normalized_visible_text(str(queued.get("text") or ""))
            if text:
                texts.add(text)
    return frozenset(texts)


def _clear_saved_composer_attachments(
    page: Any,
    *,
    managed_texts: frozenset[str],
    job_id: str,
    limit: int = 50,
    removal_timeout: int = 5_000,
) -> int:
    """Remove attachments restored by VK from an unfinished composer draft.

    VK persists a draft after a failed music lookup.  Without this cleanup,
    every retry uploads the same image once more and eventually publishes a
    carousel of clones.
    """
    scope = _first_visible(page, COMPOSER_SCOPE_SELECTORS)
    if scope is None:
        raise RetryablePublishError(
            "VK composer attachment scope is unavailable; retry later"
        )
    editor = _post_input(page)
    saved_text = _composer_saved_text(editor)
    attachments = _visible_attachment_items(scope)
    if (saved_text or attachments) and saved_text not in managed_texts:
        _record_admin_notice(job_id, "vk_unmanaged_saved_composer_draft")
        raise VkComposerStructureError(
            "VK composer contains an unmanaged saved draft"
        )

    removed = 0
    while attachments:
        if removed >= limit:
            raise RetryablePublishError(
                "VK saved composer attachment cleanup exceeded safe limit"
            )
        candidate = None
        try:
            controls = scope.locator(COMPOSER_ATTACHMENT_REMOVE_SELECTOR)
            count = min(int(controls.count()), limit)
        except Exception as exc:
            raise RetryablePublishError(
                "VK saved composer attachments are unavailable; retry later"
            ) from exc
        for index in range(count):
            current = controls.nth(index)
            try:
                if current.is_visible():
                    candidate = current
                    break
            except Exception as exc:
                raise RetryablePublishError(
                    "VK saved composer attachment control detached; retry later"
                ) from exc
        if candidate is None:
            try:
                attachments[0].hover(timeout=3_000, force=True)
                page.wait_for_timeout(150)
                controls = scope.locator(COMPOSER_ATTACHMENT_REMOVE_SELECTOR)
                for index in range(min(int(controls.count()), limit)):
                    current = controls.nth(index)
                    if current.is_visible():
                        candidate = current
                        break
            except Exception as exc:
                raise RetryablePublishError(
                    "VK saved composer attachment controls are unavailable; retry later"
                ) from exc
        if candidate is None:
            raise RetryablePublishError(
                "VK saved composer attachment has no removable control; retry later"
            )
        before = len(attachments)
        try:
            candidate.click(timeout=5_000, force=True, no_wait_after=True)
        except Exception as exc:
            raise RetryablePublishError(
                "VK saved composer attachment could not be removed; retry later"
            ) from exc
        removed += 1
        attachments = _wait_for_attachment_reduction(
            page,
            scope,
            before,
            timeout=removal_timeout,
        )
    if _visible_attachment_items(scope):
        raise RetryablePublishError(
            "VK saved composer attachment cleanup was incomplete; retry later"
        )
    return removed


def _wait_for_composer_attachment_count(
    page: Any,
    expected: int,
    *,
    timeout: int = 10_000,
) -> None:
    scope = _first_visible(page, COMPOSER_SCOPE_SELECTORS)
    if scope is None:
        raise RetryablePublishError(
            "VK composer attachment scope is unavailable; retry later"
        )
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if len(_visible_attachment_items(scope)) == expected:
            return
        page.wait_for_timeout(250)
    raise RetryablePublishError(
        "VK composer attachment count did not match the queue job; retry later"
    )


def _image_file_input_score(candidate: Any) -> int | None:
    accept = str(candidate.get_attribute("accept") or "").casefold()
    testid = str(candidate.get_attribute("data-testid") or "")
    accepted = tuple(
        token.strip() for token in accept.split(",") if token.strip()
    )
    if not accepted or not any(token.startswith("image/") for token in accepted):
        return None
    non_image = frozenset(
        token for token in accepted if not token.startswith("image/")
    )
    if not non_image:
        return 225 if testid == COMPOSER_DEVICE_UPLOAD_TESTID else 200
    if (
        testid != COMPOSER_DEVICE_UPLOAD_TESTID
        or not non_image.issubset(COMPOSER_DEVICE_VIDEO_ACCEPT_TOKENS)
    ):
        return None
    return 125


def _best_image_file_input(root: Any, selector: str) -> Any | None:
    try:
        inputs = root.locator(selector)
        count = int(inputs.count())
        if count > 20:
            raise RetryablePublishError(
                "VK composer has an unexpected number of image upload inputs; retry later"
            )
    except Exception as exc:
        if isinstance(exc, RetryablePublishError):
            raise
        raise RetryablePublishError(
            "VK composer image upload inputs are unavailable; retry later"
        ) from exc
    eligible: list[tuple[int, Any]] = []
    for index in range(count):
        try:
            candidate = inputs.nth(index)
            score = _image_file_input_score(candidate)
        except Exception as exc:
            raise RetryablePublishError(
                "VK composer image upload input changed during selection; retry later"
            ) from exc
        if score is not None:
            eligible.append((score, candidate))
    if not eligible:
        return None
    best_score = max(score for score, _candidate in eligible)
    best = [candidate for score, candidate in eligible if score == best_score]
    if len(best) != 1:
        raise RetryablePublishError(
            "VK composer image upload input selection is ambiguous; retry later"
        )
    return best[0]


def _composer_image_file_input(page: Any) -> Any:
    try:
        scope = _first_visible(page, COMPOSER_SCOPE_SELECTORS)
    except Exception as exc:
        raise RetryablePublishError(
            "VK composer image upload scope is unavailable; retry later"
        ) from exc
    if scope is None:
        raise RetryablePublishError(
            "VK composer image upload scope is unavailable; retry later"
        )
    selected = _best_image_file_input(scope, 'input[type="file"]')
    if selected is not None:
        return selected
    # VK currently exposes a mixed video/image input through a React portal.
    # It is acceptable only with the exact posting-screen test id and an
    # explicit image MIME type; unrelated empty-accept page inputs stay out.
    selected = _best_image_file_input(page, COMPOSER_DEVICE_UPLOAD_SELECTOR)
    if selected is not None:
        return selected
    raise RetryablePublishError(
        "VK composer image upload input is unavailable; retry later"
    )


def _set_composer_image_files(page: Any, media: list[Path]) -> None:
    try:
        upload = _composer_image_file_input(page)
        upload.set_input_files([str(path) for path in media])
    except RetryablePublishError:
        raise
    except Exception as exc:
        raise RetryablePublishError(
            "VK composer image upload failed; retry later"
        ) from exc


def _tokens(value: str) -> set[str]:
    # Unicode-aware: the catalog legitimately contains accented artist names.
    return set(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _structured_track_identity(query: str) -> tuple[str, str] | None:
    """Return artist/title when the queue query carries or maps to that identity."""
    parts = re.split(r"\s+[\N{EM DASH}\N{EN DASH}-]\s+", query.strip(), maxsplit=1)
    if len(parts) == 2 and all(part.strip() for part in parts):
        return parts[0].strip(), parts[1].strip()

    query_key = normalize_track_query(query)
    try:
        payload = json.loads(VK_MUSIC_TRACKS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tracks = payload.get("tracks", payload) if isinstance(payload, dict) else payload
    if not isinstance(tracks, list):
        return None
    matches: list[tuple[str, str]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        artist = str(track.get("artist") or "").strip()
        title = str(track.get("title") or "").strip()
        if title and normalize_track_query(f"{artist} {title}") == query_key:
            matches.append((artist, title))
    # Ambiguous catalog identities must never be guessed.
    return matches[0] if len(matches) == 1 else None


def _audio_identity_score(value: str, query: str) -> tuple[int, int] | None:
    """Score a complete identity match without silently changing versions."""
    query_tokens = _tokens(query)
    value_tokens = _tokens(value)
    if not query_tokens or not value_tokens:
        return None
    requested_variants = query_tokens & AUDIO_VARIANT_TOKENS
    displayed_variants = value_tokens & AUDIO_VARIANT_TOKENS
    if not displayed_variants <= requested_variants:
        return None
    if query_tokens <= value_tokens:
        return 0, len(value_tokens - query_tokens)

    identity = _structured_track_identity(query)
    if identity is None:
        return None
    artist, title = identity
    artist_tokens = _tokens(artist)
    title_tokens = _tokens(title)
    if not title_tokens or not title_tokens <= value_tokens:
        return None
    # VK's current audio picker can expose only the title in a row's flattened
    # accessible text even though the artist is rendered separately. Accept
    # title-only evidence only when the title has at least two distinctive
    # tokens. A one-word title still requires artist evidence, and the variant
    # guard above continues to reject an unrequested live/remix/edit.
    distinctive_title_tokens = {
        token
        for token in title_tokens
        if len(token) >= 2 and token not in AUDIO_VARIANT_TOKENS
    }
    if len(distinctive_title_tokens) < 2 and not artist_tokens & value_tokens:
        return None
    return 1, len(value_tokens - title_tokens)


def _audio_identity_matches(value: str, query: str) -> bool:
    """Match the requested catalog identity without silently changing versions."""
    return _audio_identity_score(value, query) is not None


def _normalized_visible_text(value: str) -> str:
    without_zero_width = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
    return " ".join(without_zero_width.split()).casefold()


def _locator_search_values(locator: Any) -> tuple[str, ...]:
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
    return tuple(dict.fromkeys(values))


def _locator_search_text(locator: Any) -> str:
    return " ".join(_locator_search_values(locator))


def _nested_search_values(locator: Any, selectors: tuple[str, ...]) -> tuple[str, ...]:
    values: list[str] = []
    try:
        nested = locator.locator(", ".join(selectors))
        count = min(nested.count(), 20)
    except Exception:
        return ()
    for index in range(count):
        values.extend(_locator_search_values(nested.nth(index)))
    return tuple(dict.fromkeys(values))


def _audio_row_score(locator: Any, query: str) -> tuple[int, int] | None:
    """Read both flattened and structured VK rows, retaining strict identity."""
    query_tokens = _tokens(query)
    for value in _locator_search_values(locator):
        score = _audio_identity_score(value, query)
        if score is not None:
            return score

    identity = _structured_track_identity(query)
    if identity is None:
        return None
    artist, title = identity
    artist_tokens = _tokens(artist)
    title_tokens = _tokens(title)
    artist_values = _nested_search_values(locator, AUDIO_ARTIST_SELECTORS)
    title_values = _nested_search_values(locator, AUDIO_TITLE_SELECTORS)
    if not any(artist_tokens <= _tokens(value) for value in artist_values):
        return None
    title_matches = [
        value for value in title_values if _audio_identity_score(value, title) is not None
    ]
    if not title_matches:
        return None
    evidence_tokens = _tokens(" ".join((*artist_values, *title_matches)))
    displayed_variants = evidence_tokens & AUDIO_VARIANT_TOKENS
    requested_variants = query_tokens & AUDIO_VARIANT_TOKENS
    if not displayed_variants <= requested_variants:
        return None
    return 0, len(evidence_tokens - query_tokens)


def _audio_search_queries(query: str) -> tuple[str, ...]:
    values = [query]
    identity = _structured_track_identity(query)
    if identity is not None:
        artist, title = identity
        values.extend((f"{artist} - {title}", title))
    # Never guess a title from an unstructured query. The original flattened
    # identity remains the only safe search in that case; result selection is
    # still guarded by the complete-token matcher.
    return tuple(dict.fromkeys(value.strip() for value in values if value.strip()))


def _audio_title_fallback(page: Any, search: Any, query: str) -> tuple[Any | None, int]:
    """Find an exact visible title inside the picker when VK hides row hooks.

    The fallback never searches the whole page. It first proves a common picker
    scope containing both the already-located search field and the ``Готово``
    control. This avoids clicking an identically named track in the wall feed.
    One-word titles still need artist evidence from a clickable ancestor.
    """

    identity = _structured_track_identity(query)
    if identity is None:
        return None, 0
    _, title = identity
    scopes: list[Any] = []
    for selector in (
        "xpath=ancestor::*[.//*[normalize-space(text())='Готово']][1]",
        "xpath=ancestor::*[@role='dialog'][1]",
    ):
        try:
            candidates = search.locator(selector)
            count = min(int(candidates.count()), 4)
        except (AttributeError, TypeError, ValueError):
            continue
        for index in range(count):
            scope = candidates.nth(index)
            try:
                if scope.is_visible():
                    scopes.append(scope)
            except Exception:
                continue
        if scopes:
            break
    if not scopes:
        return None, 0

    visible_titles = 0
    for scope in scopes:
        try:
            title_candidates = scope.get_by_text(title, exact=True)
            count = min(int(title_candidates.count()), 30)
        except (AttributeError, TypeError, ValueError):
            continue
        for index in range(count):
            candidate = title_candidates.nth(index)
            try:
                if not candidate.is_visible():
                    continue
            except Exception:
                continue
            visible_titles += 1
            try:
                clickable = candidate.locator(
                    "xpath=ancestor::*["
                    "@role='option' or @role='button' or self::button or self::a or "
                    "contains(translate(@class,'AUDIO','audio'),'audio')"
                    "][1]"
                )
                clickable_count = min(int(clickable.count()), 4)
            except (AttributeError, TypeError, ValueError):
                clickable_count = 0
                clickable = None
            for clickable_index in range(clickable_count):
                row = clickable.nth(clickable_index)
                try:
                    if not row.is_visible():
                        continue
                except Exception:
                    continue
                if _audio_row_score(row, query) is not None:
                    return row, visible_titles
            # A distinctive multi-token exact title is enough only inside the
            # proven picker scope. The matcher rejects one-word identities and
            # unrequested variants here.
            if _audio_identity_score(title, query) is not None:
                return candidate, visible_titles
    return None, visible_titles


def _audio_dom_diagnostics(search: Any) -> str:
    """Return structure-only picker metadata for safe production diagnosis.

    No text, values, URLs, ids, aria labels, or post contents are collected.
    The bounded signature is useful when VK renames result-row hooks again.
    """

    try:
        payload = search.evaluate(
            """
            element => {
              const clean = node => ({
                tag: String(node.tagName || '').toLowerCase(),
                role: String(node.getAttribute?.('role') || ''),
                testid: String(node.getAttribute?.('data-testid') || ''),
                className: typeof node.className === 'string'
                  ? node.className.slice(0, 160) : '',
                descendants: Number(node.querySelectorAll?.('*').length || 0),
              });
              const ancestors = [];
              let node = element;
              let root = null;
              for (let index = 0; node && index < 9; index += 1) {
                const item = clean(node);
                ancestors.push(item);
                if (!root && item.descendants >= 20) root = node;
                node = node.parentElement;
              }
              root = root || element.parentElement || element;
              const signatures = [];
              const seen = new Set();
              for (const child of Array.from(root.querySelectorAll('*')).slice(0, 400)) {
                const item = clean(child);
                if (!item.role && !item.testid && !item.className) continue;
                const key = JSON.stringify([item.tag, item.role, item.testid, item.className]);
                if (seen.has(key)) continue;
                seen.add(key);
                signatures.push(item);
                if (signatures.length >= 60) break;
              }
              return {ancestors, signatures};
            }
            """
        )
    except Exception:
        return "dom=unavailable"
    if not isinstance(payload, dict):
        return "dom=invalid"

    def sanitized(items: Any, limit: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(items, list):
            return result
        for raw in items[:limit]:
            if not isinstance(raw, dict):
                continue
            item: dict[str, Any] = {}
            for key in ("tag", "role", "testid", "className"):
                value = re.sub(r"[^0-9A-Za-z_:\- ]", "", str(raw.get(key) or ""))
                item[key] = value[:160]
            try:
                item["descendants"] = min(max(int(raw.get("descendants", 0)), 0), 9999)
            except (TypeError, ValueError):
                item["descendants"] = 0
            result.append(item)
        return result

    safe = {
        "ancestors": sanitized(payload.get("ancestors"), 9),
        "signatures": sanitized(payload.get("signatures"), 60),
    }
    return "dom=" + json.dumps(safe, ensure_ascii=True, separators=(",", ":"))[:6000]


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
        if visible and _audio_row_score(candidate, query) is not None:
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
        "VK did not confirm the requested audio attachment; retry later "
        f"({_audio_trigger_diagnostics(page)})"
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
        if visible and _locator_or_ancestor_audio_matches(candidate, query):
            return True
    return False


def _locator_or_ancestor_audio_matches(
    candidate: Any,
    query: str,
    *,
    max_depth: int = 8,
) -> bool:
    """Match a music control against its bounded containing post structure."""

    current = candidate
    for _ in range(max_depth + 1):
        if _audio_row_score(current, query) is not None:
            return True
        try:
            if current.get_attribute("data-testid") == "post":
                break
            parent = current.locator("xpath=..")
            if int(parent.count()) <= 0:
                break
        except (AttributeError, TypeError, ValueError):
            break
        current = parent
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
        "VK publish was attempted, but no new post matching the job was confirmed"
    )


def _first_visible(page: Any, selectors: tuple[str, ...]) -> Any | None:
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                return candidate
    return None


def _audio_search_is_file_picker(search: Any) -> bool:
    try:
        dialogs = search.locator("xpath=ancestor::*[@role='dialog'][1]")
        count = min(int(dialogs.count()), 4)
    except (AttributeError, TypeError, ValueError):
        return False
    for index in range(count):
        dialog = dialogs.nth(index)
        try:
            markers = dialog.locator(
                '[data-testid="posting_file_attach_button"], '
                '[data-testid="docs_list_placeholder"]'
            )
            if int(markers.count()) > 0:
                return True
        except (AttributeError, TypeError, ValueError):
            continue
    return False


def _audio_search_input(page: Any, timeout: int = 10_000) -> Any | None:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        candidate = _first_visible(page, AUDIO_SEARCH_SELECTORS)
        if candidate is not None and not _audio_search_is_file_picker(candidate):
            return candidate
        page.wait_for_timeout(250)
    return None


def _audio_trigger_diagnostics(page: Any) -> str:
    try:
        values = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('[data-testid]'))
              .filter(element => element.offsetParent !== null)
              .map(element => String(element.getAttribute('data-testid') || ''))
              .filter(value => /audio|attach|posting|modal/i.test(value))
              .slice(0, 100)
            """
        )
    except Exception:
        return "triggers=unavailable"
    if not isinstance(values, list):
        return "triggers=invalid"
    safe = []
    for raw in values:
        value = re.sub(r"[^0-9A-Za-z_:\-]", "", str(raw))[:120]
        if value and value not in safe:
            safe.append(value)
    return "triggers=" + ",".join(safe[:60])


def _open_audio_picker(page: Any) -> Any:
    existing = _audio_search_input(page, timeout=500)
    if existing is not None:
        return existing

    composer_scope = _first_visible(
        page,
        (
            '[data-testid="posting_modal_box"]',
            '[role="dialog"][data-testid*="posting"]',
        ),
    )
    if composer_scope is None:
        raise RetryablePublishError(
            "VK audio search input composer scope is unavailable; retry later"
        )

    trigger = _first_visible(
        composer_scope,
        AUDIO_PICKER_TRIGGER_SELECTORS,
    )
    if trigger is not None:
        trigger.click(timeout=5_000, force=True, no_wait_after=True)
        page.wait_for_timeout(1_000)
        search = _audio_search_input(page, timeout=8_000)
        if search is not None:
            return search
        try:
            trigger.evaluate("element => element.click()")
            page.wait_for_timeout(1_000)
            search = _audio_search_input(page, timeout=8_000)
            if search is not None:
                return search
        except Exception:
            pass

    for label in ("Музыка", "Аудиозапись", "Аудио"):
        locator = composer_scope.get_by_text(label, exact=True)
        if locator.count() and locator.last.is_visible():
            locator.last.click(timeout=5_000, force=True, no_wait_after=True)
            page.wait_for_timeout(1_000)
            search = _audio_search_input(page, timeout=5_000)
            if search is not None:
                return search

    # A coordinate click used to work for VK's fixed layout, but now lands on
    # the file picker. Never guess an attachment type by screen position.
    raise RetryablePublishError(
        "VK audio search input is unavailable; retry later "
        f"({_audio_trigger_diagnostics(page)})"
    )


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
    query_tokens = _tokens(query)
    if not query_tokens:
        raise RetryablePublishError("VK track query has no searchable tokens")
    # Playwright locators are live: keep one locator while changing the search
    # query so compatibility mocks and the real DOM observe the same row set.
    rows = page.locator(", ".join(AUDIO_RESULT_SELECTORS))
    selected = None
    visible_title_candidates = 0
    for search_query in _audio_search_queries(query):
        try:
            search.fill(search_query, timeout=10_000)
        except Exception as exc:
            raise RetryablePublishError(
                "VK audio search input is not ready; retry later"
            ) from exc
        page.wait_for_timeout(3_000)
        scored: list[tuple[int, int, int]] = []
        for index in range(min(rows.count(), 60)):
            row = rows.nth(index)
            try:
                if not row.is_visible():
                    continue
            except Exception:
                # Compatibility with VK's older locator facade. Selection is
                # still guarded by the complete identity check below.
                pass
            score = _audio_row_score(row, query)
            if score is not None:
                scored.append((*score, index))
        if scored:
            selected = rows.nth(min(scored)[2])
            break
        selected, candidate_count = _audio_title_fallback(page, search, query)
        visible_title_candidates = max(visible_title_candidates, candidate_count)
        if selected is not None:
            break
    if selected is None:
        try:
            row_count = min(int(rows.count()), 999)
        except (TypeError, ValueError):
            row_count = 0
        raise RetryablePublishError(
            "no matching VK audio result; retry later "
            f"(rows={row_count}, exact_titles={visible_title_candidates}, "
            f"{_audio_dom_diagnostics(search)})"
        )
    selected.click(timeout=10_000)
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
                    page,
                    job["text"],
                    job["track_query"],
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
            _clear_saved_composer_attachments(
                page,
                managed_texts=_managed_saved_draft_texts(job),
                job_id=job["job_id"],
            )
            _wait_for_composer_attachment_count(page, 0)
            if media:
                _set_composer_image_files(page, media)
                page.wait_for_timeout(4_000)
            _wait_for_composer_attachment_count(page, len(media))
            _post_input(page).fill(job["text"])
            page.wait_for_timeout(700)
            _click_first_text(page, (NEXT_TEXT,))
            page.wait_for_timeout(2_500)
            _attach_track(page, job["track_query"])
            _publish_and_confirm(page, job, publication_before)
        finally:
            context.close()


def _publication_cooldown_remaining(
    now: datetime | None = None,
) -> int:
    """Return the durable post-success throttle in whole seconds.

    The consumer timer intentionally runs often so browser failures recover
    promptly. Publication receipts are the authoritative success signal, so a
    separate receipt-backed throttle prevents an outage backlog from becoming
    a burst of wall posts when VK recovers.
    """
    if PUBLISH_MIN_INTERVAL_SECONDS == 0:
        return 0
    receipts = publication_receipts(QUEUE_DIR)
    if not receipts:
        return 0
    latest = max(
        datetime.fromisoformat(item["published_at"].replace("Z", "+00:00"))
        for item in receipts
    )
    current = now or datetime.now(timezone.utc)
    remaining = PUBLISH_MIN_INTERVAL_SECONDS - (current - latest).total_seconds()
    return max(0, math.ceil(remaining))


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
    try:
        cooldown_remaining = _publication_cooldown_remaining()
    except Exception as exc:
        print(f"VK publisher cannot validate publication cooldown: {exc}", flush=True)
        return 75
    if cooldown_remaining:
        print(
            "VK publisher publication cooldown active "
            f"remaining_seconds={cooldown_remaining}",
            flush=True,
        )
        return 0
    allowed_community_url()
    return consume_once(QUEUE_DIR, GROUP_ID, publish_job)


def _reconcile_confirmed_unresolved(job: dict[str, Any]) -> None:
    confirmed = {
        receipt["job_id"]: receipt
        for receipt in publication_receipts(QUEUE_DIR)
    }
    existing = confirmed.get(job["job_id"])
    if existing is not None and (
        existing["producer"] != job["producer"]
        or existing["source_ref"] != job["source_ref"]
    ):
        raise RuntimeError(
            "confirmed VK receipt does not match the unresolved attempt"
        )
    if existing is None:
        _record_publication_receipt(QUEUE_DIR, job)
    if _unresolved_publication_attempt():
        raise RuntimeError("confirmed VK receipt did not resolve publication marker")


def _inspect_unresolved_publication(*, reconcile_confirmed: bool = False) -> int:
    attempt = _load_publication_attempt()
    if attempt is None:
        raise RuntimeError("no unresolved VK publication attempt")
    job_id = attempt["job_id"]
    job_dir = None
    state = ""
    for candidate_state in ("failed", "processing", "pending", "done"):
        candidate = QUEUE_DIR / candidate_state / job_id
        if candidate.is_dir() and not candidate.is_symlink():
            job_dir = candidate
            state = candidate_state
            break
    if job_dir is None:
        raise RuntimeError("unresolved VK job directory is unavailable")
    job = validate_job(job_dir, GROUP_ID)
    if not PROFILE_DIR.is_dir() or PROFILE_DIR.is_symlink():
        raise RuntimeError("authorized browser profile is unavailable")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=HEADLESS,
            viewport={"width": 1400, "height": 900},
        )
        try:
            page = context.new_page()
            page.goto(allowed_community_url(), wait_until="domcontentloaded")
            page.wait_for_timeout(3_000)
            if _authentication_required(page):
                raise VkAuthenticationRequiredError(
                    "VK browser session authentication is required"
                )
            evidence = _published_post_evidence(
                page,
                job["text"],
                job["track_query"],
            )
            exact_visible = 0
            exact_ancestors: list[dict[str, str]] = []
            candidates = page.get_by_text(job["text"], exact=True)
            for index in range(min(candidates.count(), 20)):
                candidate = candidates.nth(index)
                try:
                    if not candidate.is_visible():
                        continue
                    exact_visible += 1
                    raw = candidate.evaluate(
                        """
                        element => {
                          const result = [];
                          let node = element;
                          for (let index = 0; node && index < 8; index += 1) {
                            result.push({
                              tag: String(node.tagName || '').toLowerCase(),
                              role: String(node.getAttribute?.('role') || ''),
                              testid: String(node.getAttribute?.('data-testid') || ''),
                              className: typeof node.className === 'string'
                                ? node.className.slice(0, 160) : '',
                            });
                            node = node.parentElement;
                          }
                          return result;
                        }
                        """
                    )
                    if isinstance(raw, list):
                        for item in raw[:8]:
                            if not isinstance(item, dict):
                                continue
                            exact_ancestors.append(
                                {
                                    key: re.sub(
                                        r"[^0-9A-Za-z_:\- ]",
                                        "",
                                        str(item.get(key) or ""),
                                    )[:160]
                                    for key in ("tag", "role", "testid", "className")
                                }
                            )
                    break
                except Exception:
                    continue
            visible_testids = page.evaluate(
                """
                () => Array.from(document.querySelectorAll('[data-testid]'))
                  .filter(element => element.offsetParent !== null)
                  .map(element => String(element.getAttribute('data-testid') || ''))
                  .filter(value => /post|feed|wall|audio|music/i.test(value))
                  .slice(0, 120)
                """
            )
        finally:
            context.close()

    safe_testids: list[str] = []
    if isinstance(visible_testids, list):
        for raw in visible_testids:
            value = re.sub(r"[^0-9A-Za-z_:\-]", "", str(raw))[:120]
            if value and value not in safe_testids:
                safe_testids.append(value)
    matching_ids = sorted(evidence.identified)
    confirmed = bool(matching_ids or evidence.anonymous_count)
    reconciled = False
    if reconcile_confirmed and confirmed:
        _reconcile_confirmed_unresolved(job)
        reconciled = True
    print(
        json.dumps(
            {
                "schema": "vk_unresolved_inspection.v1",
                "job_id": job_id,
                "queue_state": state,
                "confirmed_matching_post": confirmed,
                "reconciled": reconciled,
                "matching_post_ids": matching_ids,
                "anonymous_matching_posts": evidence.anonymous_count,
                "exact_text_visible": exact_visible,
                "exact_text_ancestors": exact_ancestors,
                "visible_testids": safe_testids,
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0 if confirmed else 75


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone allowlisted VK queue consumer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("consume-queue")
    inspect = sub.add_parser("inspect-unresolved")
    inspect.add_argument("--reconcile-confirmed", action="store_true")
    requeue = sub.add_parser("requeue-failed")
    requeue.add_argument("job_id")
    args = parser.parse_args()
    if args.command == "consume-queue":
        raise SystemExit(consume_queue())
    if args.command == "inspect-unresolved":
        raise SystemExit(
            _inspect_unresolved_publication(
                reconcile_confirmed=args.reconcile_confirmed,
            )
        )
    path = requeue_failed(QUEUE_DIR, args.job_id, GROUP_ID)
    print(f"Requeued: {path}")


if __name__ == "__main__":
    main()
