from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

import main


load_dotenv()

VK_BROWSER_PROFILE_DIR = Path(os.getenv("VK_BROWSER_PROFILE_DIR", "data/vk_browser_profile"))
VK_BROWSER_PAYLOAD_DIR = Path(os.getenv("VK_BROWSER_PAYLOAD_DIR", "data/vk_browser_payloads"))
VK_BROWSER_HEADLESS = os.getenv("VK_BROWSER_HEADLESS", "false").strip().lower() in {"1", "true", "yes", "on"}
VK_COMMUNITY_URL = os.getenv("VK_COMMUNITY_URL", f"https://vk.com/club{os.getenv('VK_GROUP_ID', '').strip()}")
VK_VPS_DB_SCP = os.getenv("VK_VPS_DB_SCP", "")

CREATE_TEXT = "\u0421\u043e\u0437\u0434\u0430\u0442\u044c"
POST_TEXTS = ("\u041f\u043e\u0441\u0442", "\u0417\u0430\u043f\u0438\u0441\u044c", "\u041f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u044f")
NEXT_TEXT = "\u0414\u0430\u043b\u0435\u0435"
DONE_TEXT = "\u0413\u043e\u0442\u043e\u0432\u043e"
PUBLISH_TEXT = "\u041e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u0442\u044c"
POST_INPUT_LABEL = "\u041d\u0430\u043f\u0438\u0448\u0438\u0442\u0435 \u0447\u0442\u043e-\u043d\u0438\u0431\u0443\u0434\u044c..."


def _tokens(value: str) -> set[str]:
    return {part for part in re.split(r"[^0-9A-Za-z\u0400-\u04ff]+", value.lower()) if len(part) >= 3}


def _track_score(row_text: str, payload: dict[str, Any]) -> int:
    track = payload.get("track") or {}
    artist = str(track.get("artist") or "").strip().lower()
    title = str(track.get("title") or "").strip()
    row = row_text.lower()
    score = 0
    if artist and artist in row:
        score += 100
    title_tokens = _tokens(title)
    row_tokens = _tokens(row_text)
    score += 10 * len(title_tokens & row_tokens)
    for hint in ("mercury", "beats", "remix", "zavtra"):
        if hint in title.lower() and hint in row:
            score += 15
    return score


def ensure_playwright() -> Any:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r requirements.txt && python -m playwright install chromium"
        ) from exc
    return sync_playwright


def click_first_text(page: Any, labels: tuple[str, ...], *, timeout: int = 15000) -> str:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        for label in labels:
            locator = page.get_by_text(label, exact=True)
            try:
                if locator.count() and locator.first.is_visible():
                    locator.first.click(timeout=min(timeout, 3000))
                    return label
            except Exception:
                continue
        page.wait_for_timeout(250)
    raise RuntimeError(f"VK control not found. Tried: {', '.join(labels)}")


def open_create_menu(page: Any) -> None:
    publish_block_button = page.locator('[data-testid="group_publish_block"] button').first
    if publish_block_button.count() and publish_block_button.is_visible():
        for _ in range(3):
            publish_block_button.click(timeout=15000, force=True)
            page.wait_for_timeout(1000)
            expanded = publish_block_button.locator("xpath=..").get_attribute("aria-expanded")
            if expanded == "true":
                return
        return
    click_first_text(page, (CREATE_TEXT,))
    page.wait_for_timeout(700)


def find_post_input(page: Any) -> Any:
    candidates = (
        page.locator(f'[aria-label="{POST_INPUT_LABEL}"]'),
        page.locator('[contenteditable="true"][role="textbox"]'),
        page.locator('[contenteditable="true"]'),
    )
    for locator in candidates:
        try:
            if locator.count() and locator.last.is_visible():
                return locator.last
        except Exception:
            continue
    raise RuntimeError("VK post editor input not found")


def save_debug_screenshot(page: Any, draft_id: int) -> Path:
    VK_BROWSER_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = VK_BROWSER_PAYLOAD_DIR / f"draft-{draft_id}-debug.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def browser_login() -> None:
    sync_playwright = ensure_playwright()
    VK_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(VK_BROWSER_PROFILE_DIR),
            headless=VK_BROWSER_HEADLESS,
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.goto(VK_COMMUNITY_URL or "https://vk.com", wait_until="domcontentloaded")
        print("VK browser profile is open.")
        print(f"Profile dir: {VK_BROWSER_PROFILE_DIR}")
        print("Log in as the admin user, open the VOID community, then press Enter here.")
        input()
        context.close()


def open_community() -> None:
    sync_playwright = ensure_playwright()
    VK_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(VK_BROWSER_PROFILE_DIR),
            headless=False,
            no_viewport=True,
            args=["--start-maximized", "--start-fullscreen"],
        )
        page = context.new_page()
        page.goto(VK_COMMUNITY_URL or "https://vk.com", wait_until="domcontentloaded")
        print("VK community is open fullscreen. Press Enter to close.")
        input()
        context.close()


def build_browser_payload(draft_id: int) -> dict[str, Any]:
    draft = main.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"Draft #{draft_id} not found")

    ok, reason = main.quality_check(draft["post"])
    if not ok:
        raise RuntimeError(f"Draft #{draft_id} blocked by quality check: {reason}")

    existing = main.get_vk_post_for_draft(draft_id)
    if existing:
        raise RuntimeError(f"Draft #{draft_id} already published to VK as post_id={existing['post_id']}")

    VK_BROWSER_PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    images = main.generate_post_images_sync(draft)
    image_path = ""
    if images:
        image_file = VK_BROWSER_PAYLOAD_DIR / f"draft-{draft_id}.png"
        image_file.write_bytes(images[0])
        image_path = str(image_file.resolve())

    track = main.choose_vk_music_track(draft)
    track_query = ""
    if track:
        artist = str(track.get("artist", "")).strip()
        title = str(track.get("title", "")).strip()
        track_query = f"{artist} {title}".strip()

    payload = {
        "draft_id": draft_id,
        "community_url": VK_COMMUNITY_URL,
        "post_as": "community",
        "text": draft["post"],
        "image_path": image_path,
        "track": track or {},
        "track_query": track_query,
    }
    payload_file = VK_BROWSER_PAYLOAD_DIR / f"draft-{draft_id}.json"
    payload_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def sync_db_from_vps() -> None:
    if not VK_VPS_DB_SCP:
        return

    db_path = Path(main.DB_PATH)
    if db_path.exists():
        backup = db_path.with_name(f"{db_path.name}.localbak")
        shutil.copy2(db_path, backup)
    subprocess.run(["scp", VK_VPS_DB_SCP, str(db_path)], check=True)


def mark_vps_browser_published(draft_id: int, track: dict[str, Any] | None = None) -> None:
    match = re.match(r"([^:]+):(.*/)[^/]+$", VK_VPS_DB_SCP)
    if not match:
        return

    host = match.group(1)
    remote_dir = match.group(2).rstrip("/")
    track_json = json.dumps(track or {}, ensure_ascii=False)
    script = f"""
import json
import main
main.mark_vk_published(
    {int(draft_id)},
    0,
    main.vk_owner_id_from_group_id(main.VK_GROUP_ID),
    ["browser"],
    json.loads({json.dumps(track_json, ensure_ascii=False)}),
)
""".strip()
    subprocess.run(
        ["ssh", host, f"cd {remote_dir} && /opt/void_entity/venv/bin/python -"],
        input=script,
        text=True,
        check=True,
    )


def vps_target() -> tuple[str, str]:
    match = re.match(r"([^:]+):(.*/)[^/]+$", VK_VPS_DB_SCP)
    if not match:
        raise RuntimeError(f"Cannot parse VK_VPS_DB_SCP: {VK_VPS_DB_SCP}")
    return match.group(1), match.group(2).rstrip("/")


def make_remote_scheduled_draft() -> int:
    host, remote_dir = vps_target()
    script = """
import asyncio
import main
print(asyncio.run(main.make_scheduled_rubric_draft_once()))
""".strip()
    result = subprocess.run(
        ["ssh", host, f"cd {remote_dir} && /opt/void_entity/venv/bin/python -"],
        input=script,
        text=True,
        check=True,
        capture_output=True,
    )
    print(result.stdout.strip())
    match = re.search(r"#(\\d+)", result.stdout)
    if not match:
        raise RuntimeError("Remote scheduled draft did not return a draft id")
    return int(match.group(1))


def open_payload(payload_path: str, *, publish: bool = False) -> None:
    sync_playwright = ensure_playwright()
    payload = json.loads(Path(payload_path).read_text(encoding="utf-8"))
    VK_BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(VK_BROWSER_PROFILE_DIR),
            headless=VK_BROWSER_HEADLESS,
            viewport={"width": 1400, "height": 900},
        )
        page = context.new_page()
        page.goto(payload.get("community_url") or VK_COMMUNITY_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)

        try:
            open_create_menu(page)
            click_first_text(page, POST_TEXTS)
            page.wait_for_timeout(1200)

            image_path = payload.get("image_path")
            if image_path:
                page.locator("input[type=file]").last.set_input_files(image_path)
                page.wait_for_timeout(4000)

            find_post_input(page).fill(payload["text"])
            page.wait_for_timeout(700)
            click_first_text(page, (NEXT_TEXT,))
            page.wait_for_timeout(2500)
        except Exception as exc:
            screenshot = save_debug_screenshot(page, int(payload["draft_id"]))
            raise RuntimeError(f"VK composer setup failed; screenshot: {screenshot}") from exc

        selected_track = ""
        track_query = str(payload.get("track_query") or "").strip()
        if track_query:
            page.mouse.click(525, 536)
            page.wait_for_timeout(1500)
            page.locator('[data-testid="posting_audio_search_audio_input"]').fill(track_query)
            page.wait_for_timeout(5000)
            rows = page.locator('[data-testid="posting_audio_audio_track_row"]')
            row_texts = [rows.nth(i).inner_text(timeout=2000) for i in range(min(rows.count(), 30))]
            best_index = -1
            best_score = 0
            for index, row_text in enumerate(row_texts):
                score = _track_score(row_text, payload)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index >= 0 and best_score >= 100:
                selected_track = row_texts[best_index].replace("\n", " - ")
                rows.nth(best_index).click(timeout=10000)
                page.wait_for_timeout(800)
                page.get_by_text(DONE_TEXT, exact=True).last.click(timeout=10000)
                page.wait_for_timeout(1500)
            else:
                print("Could not confidently select a VK audio result.")

        print("Prepared VK post in the browser.")
        print(f"Draft: #{payload['draft_id']}")
        print(f"Image: {payload.get('image_path') or 'none'}")
        print(f"Track query: {payload.get('track_query') or 'none'}")
        print(f"Selected track: {selected_track or 'none'}")
        if publish:
            page.get_by_text(PUBLISH_TEXT, exact=True).last.click(timeout=15000)
            page.wait_for_timeout(6000)
            main.mark_vk_published(
                int(payload["draft_id"]),
                0,
                main.vk_owner_id_from_group_id(main.VK_GROUP_ID),
                attachments=["browser"],
                music_track=payload.get("track") or {},
            )
            try:
                mark_vps_browser_published(int(payload["draft_id"]), payload.get("track") or {})
            except Exception as exc:
                print(f"Remote VK duplicate marker failed: {type(exc).__name__}: {exc}")
            print("Published VK post from the browser.")
        else:
            print(f"Review the visible VK composer and click '{PUBLISH_TEXT}' manually if everything is OK.")
            print("Press Enter here after you finish or close the composer.")
            input()
        context.close()


def publish_draft(draft_id: int, *, sync_db: bool = True) -> None:
    if sync_db:
        sync_db_from_vps()
    payload = build_browser_payload(draft_id)
    payload_path = VK_BROWSER_PAYLOAD_DIR / f"draft-{draft_id}.json"
    open_payload(str(payload_path), publish=True)


def publish_scheduled() -> None:
    draft_id = make_remote_scheduled_draft()
    publish_draft(draft_id, sync_db=True)


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="VOID VK browser publisher helper")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Open persistent VK browser profile and wait for manual login")
    sub.add_parser("community", help="Open the VK community fullscreen and keep it open")

    prepare = sub.add_parser("prepare-draft", help="Generate browser-publish payload for a draft")
    prepare.add_argument("draft_id", type=int)

    open_cmd = sub.add_parser("open-payload", help="Open VK community with a prepared payload")
    open_cmd.add_argument("payload_path")
    open_cmd.add_argument("--publish", action="store_true", help="Click the final VK publish button automatically")

    publish_cmd = sub.add_parser("publish-draft", help="Prepare and publish a draft through the logged-in VK browser")
    publish_cmd.add_argument("draft_id", type=int)
    publish_cmd.add_argument("--no-sync", action="store_true", help="Do not scp void.db from the VPS before preparing")

    sub.add_parser("publish-scheduled", help="Ask the VPS for the current scheduled rubric draft and publish it to VK")

    args = parser.parse_args()
    if args.command == "login":
        browser_login()
    elif args.command == "community":
        open_community()
    elif args.command == "prepare-draft":
        payload = build_browser_payload(args.draft_id)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.command == "open-payload":
        open_payload(args.payload_path, publish=args.publish)
    elif args.command == "publish-draft":
        publish_draft(args.draft_id, sync_db=not args.no_sync)
    elif args.command == "publish-scheduled":
        publish_scheduled()


if __name__ == "__main__":
    main_cli()
