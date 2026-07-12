"""VOID scheduled content producer. Creates queue jobs and never opens a browser."""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

import main
from vk_publish_queue import build_job, enqueue_job

QUEUE_DIR = Path(os.getenv("VK_PUBLISH_QUEUE_DIR", "/var/lib/void-vk-publisher/queue"))


def parse_scheduled_draft_id(response: str) -> int:
    match = re.search(r"#(\d+)", response)
    if not match:
        raise RuntimeError("Scheduled draft response did not return a draft id")
    return int(match.group(1))


def enqueue_draft(draft_id: int) -> Path:
    draft = main.get_draft(draft_id)
    if not draft:
        raise RuntimeError(f"Draft #{draft_id} not found")
    ok, reason = main.quality_check(draft["post"])
    if not ok:
        raise RuntimeError(f"Draft #{draft_id} blocked: {reason}")
    images = main.generate_post_images_sync(draft)
    media = {f"image-{index}.png": content for index, content in enumerate(images[:4], start=1)}
    track = main.choose_vk_music_track(draft) or {}
    track_query = f"{track.get('artist', '')} {track.get('title', '')}".strip()
    job = build_job(producer="void", target_group_id=str(main.VK_GROUP_ID), text=draft["post"], media=list(media), track_query=track_query, dedupe_key=f"void-draft:{draft_id}", source_ref=f"void:draft:{draft_id}")
    return enqueue_job(QUEUE_DIR, job, media)


def produce_scheduled() -> Path:
    response = asyncio.run(main.make_scheduled_rubric_draft_once())
    draft_id = parse_scheduled_draft_id(response)
    path = enqueue_draft(draft_id)
    print(f"Queued VOID VK draft #{draft_id}: {path}")
    return path


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="VOID VK queue producer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("produce-scheduled")
    draft = sub.add_parser("enqueue-draft")
    draft.add_argument("draft_id", type=int)
    args = parser.parse_args()
    if args.command == "produce-scheduled":
        produce_scheduled()
    else:
        print(enqueue_draft(args.draft_id))


if __name__ == "__main__":
    main_cli()
