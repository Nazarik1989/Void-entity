"""Strict filesystem contract shared by VK queue producers and consumer."""
from __future__ import annotations

import hashlib
import errno
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

SCHEMA = "vk_publish_job.v1"
REQUIRED_FIELDS = frozenset({"schema", "job_id", "producer", "target_group_id", "text", "media", "track_query", "created_at", "not_before", "dedupe_key", "source_ref"})
OPTIONAL_FIELDS = frozenset({"plan_id", "editorial"})
FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
PRODUCERS = frozenset({"naz", "void"})
STATES = ("pending", "processing", "done", "failed")
MAX_TEXT_LENGTH = 16_000
MAX_MEDIA_COUNT = 4
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_TRACK_QUERY_LENGTH = 300
MAX_DEDUPE_KEY_LENGTH = 256
RECENT_TRACK_LIMIT = 8
TRACK_HISTORY_FILENAME = "recent-tracks.json"
TRACK_HISTORY_BACKFILL_MARKER = ".track-history-v2-complete"
LEGACY_RECEIPT_HISTORY_SCHEMA = "vk_publish_job.v2"
PUBLICATION_RECEIPT_SCHEMA = "vk_publication_receipt.v1"
PUBLICATION_RECEIPTS_DIRNAME = "published"
PUBLICATION_RECEIPT_BACKFILL_MARKER = ".backfill-v1-complete"
PUBLICATION_RECEIPT_FIELDS = frozenset(
    {"schema", "job_id", "producer", "source_ref", "published_at"}
)
RETRY_DELAY_SECONDS = 30 * 60
JOB_ID_RE = re.compile(r"^(naz|void)-[0-9a-f]{24}$")


class QueueValidationError(ValueError):
    pass


class DuplicateJobError(QueueValidationError):
    pass


class DuplicateTrackError(QueueValidationError):
    pass


class RetryablePublishError(RuntimeError):
    """A pre-publish failure that must leave the job available for retry."""


def normalize_track_query(value: str) -> str:
    return " ".join(re.findall(r"[0-9a-zа-яё]+", str(value).casefold()))


def recent_track_keys(
    queue_root: Path,
    limit: int | None = RECENT_TRACK_LIMIT,
) -> list[str]:
    """Return published track keys in least-to-most-recent order.

    ``None`` exposes the complete distinct LRU history to catalog-aware
    producers. The numeric default keeps legacy last-eight readers compatible.
    """
    if limit is not None and limit <= 0:
        return []
    path = Path(queue_root) / TRACK_HISTORY_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueValidationError("shared VK track history is unavailable") from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"tracks"}
        or not isinstance(payload["tracks"], list)
    ):
        raise QueueValidationError("shared VK track history is invalid")
    keys: list[str] = []
    for item in payload["tracks"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"key"}
            or not isinstance(item["key"], str)
        ):
            raise QueueValidationError("shared VK track history is invalid")
        key = item["key"]
        if not key or normalize_track_query(key) != key or key in keys:
            raise QueueValidationError("shared VK track history is invalid")
        keys.append(key)
    return keys if limit is None else keys[-limit:]


def _record_published_track(queue_root: Path, job: dict[str, Any]) -> None:
    queue_root = Path(queue_root)
    path = queue_root / TRACK_HISTORY_FILENAME
    key = normalize_track_query(job["track_query"])
    existing = recent_track_keys(queue_root, None)
    keys = [item for item in existing if item != key]
    keys.append(key)
    payload = {"tracks": [{"key": item} for item in keys]}
    temp = queue_root / f".{TRACK_HISTORY_FILENAME}.tmp-{uuid.uuid4().hex}"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o644)
    os.replace(temp, path)


def _validated_receipt_history_job(
    job_dir: Path,
    allowed_group_id: str,
) -> dict[str, Any]:
    """Validate a receipt-backed job, including one known legacy shape.

    The legacy ``metadata`` field was written by an older producer and is not
    part of the current runtime contract. It is ignored only while rebuilding
    history from an authoritative publication receipt; enqueue and publish
    validation remain strict.
    """
    try:
        return validate_job(job_dir, allowed_group_id)
    except QueueValidationError as current_error:
        job_file = Path(job_dir) / "job.json"
        if job_dir.is_symlink() or job_file.is_symlink() or not job_file.is_file():
            raise current_error
        try:
            payload = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise current_error
        if (
            not isinstance(payload, dict)
            or set(payload) != REQUIRED_FIELDS | {"metadata"}
            or payload.get("schema") != LEGACY_RECEIPT_HISTORY_SCHEMA
        ):
            raise current_error
        try:
            projected = {key: payload[key] for key in REQUIRED_FIELDS}
            projected["schema"] = SCHEMA
            job = _validate_shape(projected, allowed_group_id)
        except QueueValidationError:
            raise current_error
        if job["job_id"] != Path(job_dir).name:
            raise current_error
        return job


def _backfill_full_track_history(queue_root: Path, allowed_group_id: str) -> None:
    """Expand legacy last-eight state from validated publication receipts."""
    queue_root = Path(queue_root)
    marker = queue_root / TRACK_HISTORY_BACKFILL_MARKER
    if marker.is_file() and not marker.is_symlink():
        return
    # Corrupt existing state remains fail-closed instead of being overwritten.
    ordered = recent_track_keys(queue_root, None)
    receipts = sorted(
        publication_receipts(queue_root),
        key=lambda item: item["published_at"],
    )
    for receipt in receipts:
        job_dir = next(
            (
                candidate
                # A receipt is authoritative even if later local bookkeeping
                # moved the already-published job to ``failed``.
                for state in ("done", "processing", "failed")
                if (candidate := queue_root / state / receipt["job_id"]).is_dir()
                and not candidate.is_symlink()
            ),
            None,
        )
        if job_dir is None:
            raise QueueValidationError(
                "confirmed VK publication job is unavailable for track history"
            )
        job = _validated_receipt_history_job(job_dir, allowed_group_id)
        if (
            job["producer"] != receipt["producer"]
            or job["source_ref"] != receipt["source_ref"]
        ):
            raise QueueValidationError(
                "confirmed VK publication receipt does not match its job"
            )
        key = normalize_track_query(job["track_query"])
        if key in ordered:
            ordered.remove(key)
        ordered.append(key)
    if ordered:
        payload = {"tracks": [{"key": item} for item in ordered]}
        temp = queue_root / f".{TRACK_HISTORY_FILENAME}.backfill-{uuid.uuid4().hex}"
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temp, 0o644)
        os.replace(temp, queue_root / TRACK_HISTORY_FILENAME)
    temp_marker = queue_root / f".{TRACK_HISTORY_BACKFILL_MARKER}.tmp-{uuid.uuid4().hex}"
    temp_marker.write_text(_utc_now() + "\n", encoding="utf-8")
    os.chmod(temp_marker, 0o640)
    os.replace(temp_marker, marker)


def _publication_receipt_path(queue_root: Path, job_id: str) -> Path:
    return Path(queue_root) / PUBLICATION_RECEIPTS_DIRNAME / f"{job_id}.json"


def _record_publication_receipt(queue_root: Path, job: dict[str, Any]) -> None:
    receipts = Path(queue_root) / PUBLICATION_RECEIPTS_DIRNAME
    receipts.mkdir(mode=0o770, parents=True, exist_ok=True)
    receipt = {
        "schema": PUBLICATION_RECEIPT_SCHEMA,
        "job_id": job["job_id"],
        "producer": job["producer"],
        "source_ref": job["source_ref"],
        "published_at": _utc_now(),
    }
    final = _publication_receipt_path(queue_root, job["job_id"])
    temp = receipts / f".{job['job_id']}.tmp-{uuid.uuid4().hex}"
    temp.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp, 0o640)
    os.replace(temp, final)


def publication_receipts(
    queue_root: Path,
    *,
    producer: str | None = None,
) -> list[dict[str, str]]:
    if producer is not None and producer not in PRODUCERS:
        raise QueueValidationError("unknown receipt producer")
    receipts = Path(queue_root) / PUBLICATION_RECEIPTS_DIRNAME
    if not receipts.exists():
        return []
    if not receipts.is_dir() or receipts.is_symlink():
        raise QueueValidationError("publication receipt directory is unavailable")
    result: list[dict[str, str]] = []
    for path in sorted(receipts.glob("*.json")):
        if not path.is_file() or path.is_symlink():
            raise QueueValidationError("invalid publication receipt path")
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueueValidationError("publication receipt is unavailable") from exc
        if not isinstance(receipt, dict) or set(receipt) != PUBLICATION_RECEIPT_FIELDS:
            raise QueueValidationError("invalid publication receipt fields")
        if receipt["schema"] != PUBLICATION_RECEIPT_SCHEMA:
            raise QueueValidationError("unknown publication receipt schema")
        if (
            not isinstance(receipt["job_id"], str)
            or not JOB_ID_RE.fullmatch(receipt["job_id"])
            or not isinstance(receipt["producer"], str)
            or receipt["producer"] not in PRODUCERS
            or not isinstance(receipt["source_ref"], str)
            or not receipt["source_ref"]
            or not isinstance(receipt["published_at"], str)
        ):
            raise QueueValidationError("invalid publication receipt")
        try:
            published_at = datetime.fromisoformat(
                receipt["published_at"].replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise QueueValidationError("invalid publication timestamp") from exc
        if published_at.tzinfo is None:
            raise QueueValidationError("publication timestamp must include timezone")
        if producer is None or receipt["producer"] == producer:
            result.append(
                {key: str(receipt[key]) for key in PUBLICATION_RECEIPT_FIELDS}
            )
    return result


def _backfill_publication_receipts(
    queue_root: Path,
    allowed_group_id: str,
) -> None:
    queue_root = Path(queue_root)
    receipts = queue_root / PUBLICATION_RECEIPTS_DIRNAME
    marker = receipts / PUBLICATION_RECEIPT_BACKFILL_MARKER
    if marker.is_file() and not marker.is_symlink():
        return
    done = queue_root / "done"
    for job_dir in sorted(done.iterdir()):
        if not job_dir.is_dir() or job_dir.is_symlink():
            continue
        job = validate_job(job_dir, allowed_group_id)
        receipt_path = _publication_receipt_path(queue_root, job["job_id"])
        if not receipt_path.exists():
            _record_publication_receipt(queue_root, job)
    receipts.mkdir(mode=0o770, parents=True, exist_ok=True)
    temp = receipts / f".backfill-v1.tmp-{uuid.uuid4().hex}"
    temp.write_text(_utc_now() + "\n", encoding="utf-8")
    os.chmod(temp, 0o640)
    os.replace(temp, marker)


def _ensure_track_is_fresh(queue_root: Path, track_query: str) -> None:
    key = normalize_track_query(track_query)
    if key in set(recent_track_keys(queue_root, RECENT_TRACK_LIMIT)):
        raise DuplicateTrackError("track was used in the last 8 published VK posts")


def canonical_job_id(producer: str, dedupe_key: str) -> str:
    if producer not in PRODUCERS:
        raise QueueValidationError("unknown producer")
    if not isinstance(dedupe_key, str) or not dedupe_key or len(dedupe_key) > MAX_DEDUPE_KEY_LENGTH:
        raise QueueValidationError("invalid dedupe_key")
    digest = hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()
    return f"{producer}-{digest[:24]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_media_name(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "://" in value:
        raise QueueValidationError("media names must be relative file names")
    path = PurePosixPath(value)
    if path.is_absolute() or len(path.parts) != 1 or value in {".", ".."}:
        raise QueueValidationError("media names must be relative file names")
    return value


def _validate_shape(job: Any, allowed_group_id: str | None = None) -> dict[str, Any]:
    if not isinstance(job, dict) or not REQUIRED_FIELDS.issubset(job) or not set(job).issubset(FIELDS):
        raise QueueValidationError("unknown or missing job fields")
    if job["schema"] != SCHEMA:
        raise QueueValidationError("unknown schema")
    if job["producer"] not in PRODUCERS:
        raise QueueValidationError("unknown producer")
    if not isinstance(job["target_group_id"], str) or not job["target_group_id"]:
        raise QueueValidationError("target_group_id must be a JSON string")
    if allowed_group_id is not None and job["target_group_id"] != str(allowed_group_id):
        raise QueueValidationError("target_group_id is not allowed")
    expected_id = canonical_job_id(job["producer"], job["dedupe_key"])
    if not isinstance(job["job_id"], str) or not JOB_ID_RE.fullmatch(job["job_id"]) or job["job_id"] != expected_id:
        raise QueueValidationError("job_id is not canonical")
    if not isinstance(job["text"], str) or not job["text"] or len(job["text"]) > MAX_TEXT_LENGTH:
        raise QueueValidationError("invalid text length")
    if (
        not isinstance(job["track_query"], str)
        or not normalize_track_query(job["track_query"])
        or len(job["track_query"]) > MAX_TRACK_QUERY_LENGTH
    ):
        raise QueueValidationError("track_query is required")
    if not all(isinstance(job[key], str) for key in ("created_at", "not_before", "source_ref")):
        raise QueueValidationError("invalid metadata")
    if not job["created_at"] or not job["source_ref"]:
        raise QueueValidationError("created_at and source_ref are required")
    if "editorial" in job and "plan_id" not in job:
        raise QueueValidationError("editorial metadata requires plan_id")
    if "plan_id" in job:
        if not isinstance(job["plan_id"], str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,64}", job["plan_id"]):
            raise QueueValidationError("invalid plan_id")
        editorial = job.get("editorial")
        if not isinstance(editorial, dict) or len(editorial) > 32:
            raise QueueValidationError("invalid editorial metadata")
        if any(
            not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", key)
            or not isinstance(value, (str, list))
            or (isinstance(value, str) and len(value) > 1000)
            or (
                isinstance(value, list)
                and (len(value) > 16 or any(not isinstance(item, str) or len(item) > 200 for item in value))
            )
            for key, value in editorial.items()
        ):
            raise QueueValidationError("editorial metadata must be string/list only")
    for key in ("created_at", "not_before"):
        if job[key]:
            try:
                parsed = datetime.fromisoformat(job[key].replace("Z", "+00:00"))
            except ValueError as exc:
                raise QueueValidationError(f"invalid {key}") from exc
            if parsed.tzinfo is None:
                raise QueueValidationError(f"{key} must include timezone")
    media = job["media"]
    if not isinstance(media, list) or len(media) > MAX_MEDIA_COUNT or len(media) != len(set(media)):
        raise QueueValidationError("invalid media attachments")
    for name in media:
        _safe_media_name(name)
    return job


def validate_job(job_dir: Path, allowed_group_id: str) -> dict[str, Any]:
    job_dir = Path(job_dir)
    job_file = job_dir / "job.json"
    if job_dir.is_symlink() or job_file.is_symlink() or not job_file.is_file():
        raise QueueValidationError("job directory and job.json must be regular")
    try:
        job = _validate_shape(json.loads(job_file.read_text(encoding="utf-8")), allowed_group_id)
    except (OSError, json.JSONDecodeError) as exc:
        raise QueueValidationError("invalid job.json") from exc
    if job["job_id"] != job_dir.name:
        raise QueueValidationError("job_id does not match directory")
    for name in job["media"]:
        path = job_dir / name
        if path.is_symlink() or not path.is_file():
            raise QueueValidationError("media must be regular files")
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise QueueValidationError("image is too large")
    return job


def _dedupe_seen(queue_root: Path, key: str, current: Path | None = None) -> bool:
    for state in STATES:
        directory = Path(queue_root) / state
        if not directory.exists():
            continue
        for job_file in directory.glob("*/job.json"):
            if current is not None and job_file.parent == current:
                continue
            if job_file.is_symlink():
                continue
            try:
                if json.loads(job_file.read_text(encoding="utf-8")).get("dedupe_key") == key:
                    return True
            except (OSError, json.JSONDecodeError, TypeError):
                continue
    return False


def build_job(*, producer: str, target_group_id: str, text: str, media: list[str], track_query: str = "", not_before: str = "", dedupe_key: str, source_ref: str, created_at: str | None = None, plan_id: str = "", editorial: dict[str, Any] | None = None) -> dict[str, Any]:
    job = {"schema": SCHEMA, "job_id": canonical_job_id(producer, dedupe_key), "producer": producer, "target_group_id": str(target_group_id), "text": text, "media": media, "track_query": track_query, "created_at": created_at or _utc_now(), "not_before": not_before, "dedupe_key": dedupe_key, "source_ref": source_ref}
    if plan_id:
        job["plan_id"] = str(plan_id)
        job["editorial"] = dict(editorial or {})
    return _validate_shape(job)


def enqueue_job(queue_root: Path, job: dict[str, Any], media: dict[str, bytes]) -> Path:
    job = _validate_shape(job)
    if set(media) != set(job["media"]):
        raise QueueValidationError("media payload does not match job.media")
    queue_root = Path(queue_root)
    _ensure_track_is_fresh(queue_root, job["track_query"])
    pending = queue_root / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    final = pending / job["job_id"]
    if final.exists():
        raise DuplicateJobError(f"job already exists: {job['job_id']}")
    temp = pending / f".{job['job_id']}.tmp-{uuid.uuid4().hex}"
    old_umask = os.umask(0o027)
    try:
        temp.mkdir(mode=0o770)
        os.chmod(temp, 0o770)
        for name, content in media.items():
            if len(content) > MAX_IMAGE_BYTES:
                raise QueueValidationError("image is too large")
            path = temp / name
            path.write_bytes(content)
            os.chmod(path, 0o640)
        job_file = temp / "job.json"
        job_file.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(job_file, 0o640)
        try:
            os.replace(temp, final)
        except OSError as exc:
            if final.exists() or exc.errno in {errno.EEXIST, errno.ENOTEMPTY}:
                raise DuplicateJobError(f"job already exists: {job['job_id']}") from exc
            raise
    except Exception:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    finally:
        os.umask(old_umask)
    return final


def requeue_failed(queue_root: Path, job_id: str, allowed_group_id: str) -> Path:
    queue_root = Path(queue_root)
    source = queue_root / "failed" / job_id
    if source.is_symlink() or not source.is_dir():
        raise QueueValidationError("failed job not found")
    if _publication_receipt_path(queue_root, job_id).is_file():
        raise DuplicateJobError("published job cannot be requeued")
    job = validate_job(source, allowed_group_id)
    if job["job_id"] != job_id:
        raise QueueValidationError("job_id mismatch")
    target = queue_root / "pending" / job_id
    if target.exists():
        raise DuplicateJobError("pending job already exists")
    error_file = source / "error.txt"
    if error_file.exists() and not error_file.is_symlink():
        error_file.unlink()
    os.replace(source, target)
    return target


def consume_once(queue_root: Path, allowed_group_id: str, publish: Callable[[dict[str, Any], list[Path]], None]) -> int:
    queue_root = Path(queue_root)
    for state in STATES:
        (queue_root / state).mkdir(parents=True, exist_ok=True)
    _backfill_publication_receipts(queue_root, allowed_group_id)
    _backfill_full_track_history(queue_root, allowed_group_id)
    for source in sorted((queue_root / "pending").iterdir()):
        if not source.is_dir() or source.is_symlink() or source.name.startswith("."):
            continue
        processing = queue_root / "processing" / source.name
        try:
            os.replace(source, processing)
        except OSError:
            continue
        try:
            job = validate_job(processing, allowed_group_id)
            if _dedupe_seen(queue_root, job["dedupe_key"], processing):
                raise DuplicateJobError("duplicate dedupe_key")
            retry_file = processing / "retry.txt"
            if (
                retry_file.is_file()
                and not retry_file.is_symlink()
                and time.time() - retry_file.stat().st_mtime < RETRY_DELAY_SECONDS
            ):
                os.replace(processing, source)
                continue
            if job["not_before"] and datetime.fromisoformat(job["not_before"].replace("Z", "+00:00")) > datetime.now(timezone.utc):
                os.replace(processing, source)
                continue
            _ensure_track_is_fresh(queue_root, job["track_query"])
            publish(job, [processing / name for name in job["media"]])
            _record_publication_receipt(queue_root, job)
            _record_published_track(queue_root, job)
            if retry_file.exists() and not retry_file.is_symlink():
                retry_file.unlink()
            os.replace(processing, queue_root / "done" / source.name)
            return 0
        except RetryablePublishError as exc:
            (processing / "retry.txt").write_text(
                f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
            )
            os.replace(processing, source)
            return 0
        except Exception as exc:
            failed_target = queue_root / "failed" / source.name
            if isinstance(exc, DuplicateJobError) and failed_target.is_dir():
                shutil.rmtree(processing)
                return 1
            (processing / "error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            os.replace(processing, failed_target)
            return 1
    return 0
