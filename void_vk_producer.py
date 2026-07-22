"""VOID scheduled content producer. Creates queue jobs and never opens a browser."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path

import main
from vk_publish_queue import (
    build_job,
    enqueue_job,
    publication_receipts,
    recent_track_keys,
)

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))
VOID_DRAFT_SOURCE_RE = re.compile(r"^void:draft:(\d+)$")


def _editorial_plan(draft) -> main.editorial_orchestrator.EditorialPlan | None:
    raw = str(draft["editorial_plan_json"] or "") if "editorial_plan_json" in draft.keys() else ""
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return None
        return main.editorial_orchestrator.EditorialPlan.from_dict(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def parse_scheduled_draft_id(response: str) -> int:
    match = re.search(r"#(\d+)", response)
    if not match:
        raise RuntimeError("Scheduled draft response did not return a draft id")
    return int(match.group(1))


def sync_published_drafts() -> list[int]:
    main.init_db()
    published_now: list[int] = []
    for receipt in publication_receipts(QUEUE_DIR, producer="void"):
        match = VOID_DRAFT_SOURCE_RE.fullmatch(receipt["source_ref"])
        if not match:
            continue
        draft_id = int(match.group(1))
        draft = main.get_draft(draft_id)
        if not draft:
            continue
        if main.mark_published(draft_id):
            main.apply_character_event(
                main.character_event_for_mode(str(draft["mode"] or ""))
            )
            main.apply_character_event("publish")
            published_now.append(draft_id)
    return published_now


def enqueue_draft(draft_id: int) -> Path:
    draft = main.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"Draft #{draft_id} not found")
    ok, reason = main.quality_check(draft["post"])
    if not ok:
        raise RuntimeError(f"Draft #{draft_id} blocked: {reason}")
    images = main.generate_post_images_sync(draft)
    media = {f"image-{index}.png": content for index, content in enumerate(images[:4], start=1)}
    track = main.choose_vk_music_track(
        draft,
        excluded_track_keys=set(recent_track_keys(QUEUE_DIR)),
    )
    if not track:
        raise RuntimeError("No suitable fresh VK music track is available; draft was not queued")
    track_query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
    plan = _editorial_plan(draft)
    job = build_job(
        producer="void",
        target_group_id=str(main.VK_GROUP_ID),
        text=draft["post"],
        media=list(media),
        track_query=track_query,
        dedupe_key=f"void-draft:{draft_id}",
        source_ref=f"void:draft:{draft_id}",
        plan_id=plan.plan_id if plan is not None else "",
        editorial=main.safe_vk_editorial_metadata(plan) if plan is not None else None,
    )
    return enqueue_job(QUEUE_DIR, job, media)


def produce_scheduled() -> Path:
    sync_published_drafts()
    response = asyncio.run(main.make_scheduled_rubric_draft_once())
    draft_id = parse_scheduled_draft_id(response)
    path = enqueue_draft(draft_id)
    print(f"Queued VOID VK draft #{draft_id}: {path}")
    return path


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="VOID VK queue producer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("produce-scheduled")
    sub.add_parser("sync-published")
    draft = sub.add_parser("enqueue-draft")
    draft.add_argument("draft_id", type=int)
    args = parser.parse_args()
    if args.command == "produce-scheduled":
        produce_scheduled()
    elif args.command == "enqueue-draft":
        print(enqueue_draft(args.draft_id))
    else:
        print(f"Synced published VOID drafts: {len(sync_published_drafts())}")


if __name__ == "__main__":
    main_cli()
