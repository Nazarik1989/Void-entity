"""Pure, read-only coordinated deploy embargo calculation for Naz and VOID."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DEPLOY_EMBARGO_SECONDS = 15 * 60
POST_SLOT_CLAIM_GRACE_SECONDS = 60
PERSISTENT_BOT_UNITS = frozenset({"naz-ai-bot.service", "void-entity.service"})
EXPECTED_UNIT_KEYS = frozenset(
    {
        "naz-ai-bot.service",
        "void-entity.service",
        "naz-vk-producer.service",
        "void-vk-producer.service",
        "void-vk-autopost.service",
    }
)
NAZ_MARKER_LABEL_TO_WORK_KEY = {
    "telegram_autopost": "naz.telegram_autopost",
    "crosspost_exchange": "naz.crosspost_exchange",
    "source_monitor": "naz.source_monitor",
    "agent_content_sync": "naz.agent_content_sync",
    "vk_embedded_producer": "naz.vk_embedded_producer",
    "vk_systemd_producer": "naz.vk_systemd_producer",
    "vk_receipt_sync": "naz.vk_receipt_sync",
}
VOID_MARKER_KIND_TO_WORK_KEY = {
    "void.telegram": "void.telegram",
    "void.crosspost": "void.crosspost",
}
EXPECTED_BOT_WORK_KEYS = frozenset(
    {*NAZ_MARKER_LABEL_TO_WORK_KEY.values(), *VOID_MARKER_KIND_TO_WORK_KEY.values()}
)

NAZ_TELEGRAM_TIMES = ("10:00", "14:00", "18:00", "22:00")
VOID_TELEGRAM_TIMES = ("12:00", "16:00", "20:00", "00:00")
DEFAULT_MARKER_DIRS = {
    # This is Naz's actual runtime default. Only hidden scheduled-work marker
    # files are inspected; DB/env/content files in this directory are ignored.
    "naz": Path("/var/lib/naz-ai-bot"),
    "void": Path("/run/void-entity-scheduled-work"),
}
DEFAULT_QUEUE_ROOT = Path("/var/lib/void-vk-publisher/queue")
DEFAULT_CONSUMER_LOCK = Path("/run/void-vk-publisher/consumer.lock")


@dataclass(frozen=True, slots=True)
class ScheduleSnapshot:
    daily_times: tuple[str, ...] = ()
    weekly_times: tuple[tuple[tuple[int, ...], str], ...] = ()


@dataclass(frozen=True, slots=True)
class NaturalSlot:
    route: str
    persona: str
    destination: str
    scheduled_at: datetime


@dataclass(frozen=True, slots=True)
class DeployPreflight:
    allowed: bool
    next_slots: tuple[NaturalSlot, ...]
    nearest_slot: NaturalSlot | None
    seconds_to_nearest_slot: int | None
    valid_until: datetime | None
    reason_codes: tuple[str, ...]
    blockers: tuple[str, ...]
    in_flight: tuple[str, ...]


def default_schedule_snapshots() -> dict[str, ScheduleSnapshot]:
    """Tracked schedules; production callers may replace them with resolved snapshots."""
    return {
        "naz.telegram": ScheduleSnapshot(daily_times=NAZ_TELEGRAM_TIMES),
        "naz.vk": ScheduleSnapshot(
            daily_times=("10:30",),
            weekly_times=(((1, 3, 6), "16:30"),),  # Tue, Thu, Sun.
        ),
        "void.telegram": ScheduleSnapshot(daily_times=VOID_TELEGRAM_TIMES),
        "void.vk": ScheduleSnapshot(
            daily_times=("13:30", "20:30"),
            weekly_times=(((4, 5), "23:30"),),  # Fri, Sat.
        ),
    }


def parse_times(value: str | Sequence[str]) -> tuple[str, ...]:
    raw_values = value.split(",") if isinstance(value, str) else value
    result: list[str] = []
    for raw in raw_values:
        item = str(raw).strip()
        try:
            hour_text, minute_text = item.split(":", maxsplit=1)
            hour, minute = int(hour_text), int(minute_text)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid natural slot: {item}") from exc
        if hour not in range(24) or minute not in range(60):
            raise ValueError(f"invalid natural slot: {item}")
        normalized = f"{hour:02d}:{minute:02d}"
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def _at(day: datetime, value: str) -> datetime:
    hour, minute = (int(part) for part in value.split(":", maxsplit=1))
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _route_candidate_times(now: datetime, snapshot: ScheduleSnapshot) -> tuple[datetime, ...]:
    daily = parse_times(snapshot.daily_times)
    weekly = tuple((tuple(days), parse_times((value,))[0]) for days, value in snapshot.weekly_times)
    if not daily and not weekly:
        raise ValueError("empty schedule")
    candidates: list[datetime] = []
    for offset in range(-1, 8):
        day = now + timedelta(days=offset)
        candidates.extend(_at(day, value) for value in daily)
        for weekdays, value in weekly:
            if not weekdays or any(index not in range(7) for index in weekdays):
                raise ValueError("invalid weekday schedule")
            if day.weekday() in weekdays:
                candidates.append(_at(day, value))
    return tuple(sorted(set(candidates)))


def _next_route_slot(now: datetime, route: str, snapshot: ScheduleSnapshot) -> NaturalSlot:
    persona, destination = route.split(".", maxsplit=1)
    upcoming = [candidate for candidate in _route_candidate_times(now, snapshot) if candidate >= now]
    if not upcoming:
        raise ValueError("no upcoming natural slot")
    return NaturalSlot(route, persona, destination, upcoming[0])


def _previous_route_slot(now: datetime, route: str, snapshot: ScheduleSnapshot) -> NaturalSlot | None:
    persona, destination = route.split(".", maxsplit=1)
    previous = [candidate for candidate in _route_candidate_times(now, snapshot) if candidate < now]
    if not previous:
        return None
    return NaturalSlot(route, persona, destination, previous[-1])


def assess_coordinated_deploy(
    now: datetime,
    *,
    schedules: Mapping[str, ScheduleSnapshot | None] | None = None,
    unit_states: Mapping[str, bool] | None,
    bot_work: Mapping[str, bool] | None,
    queue_processing_count: int | None,
    consumer_lock_held: bool | None,
    embargo_seconds: int = DEPLOY_EMBARGO_SECONDS,
    post_slot_claim_grace_seconds: int = POST_SLOT_CLAIM_GRACE_SECONDS,
) -> DeployPreflight:
    """Fail closed unless schedules and every in-flight input are known."""
    if now.tzinfo is None:
        raise ValueError("preflight time must be timezone-aware")
    current = now.astimezone(MOSCOW_TZ)
    expected_routes = tuple(default_schedule_snapshots())
    resolved = default_schedule_snapshots() if schedules is None else dict(schedules)
    reasons: list[str] = []
    blockers: list[str] = []
    next_slots: list[NaturalSlot] = []
    previous_slots: list[NaturalSlot] = []

    for route in expected_routes:
        snapshot = resolved.get(route)
        if snapshot is None:
            reasons.append("schedule_unknown")
            blockers.append(f"schedule_unknown:{route}")
            continue
        try:
            next_slots.append(_next_route_slot(current, route, snapshot))
            previous = _previous_route_slot(current, route, snapshot)
            if previous is not None:
                previous_slots.append(previous)
        except (AttributeError, TypeError, ValueError):
            reasons.append("schedule_unknown")
            blockers.append(f"schedule_unknown:{route}")
    for route in sorted(set(resolved) - set(expected_routes)):
        reasons.append("schedule_unknown")
        blockers.append(f"schedule_unknown:{route}")

    if unit_states is None or bot_work is None or queue_processing_count is None or consumer_lock_held is None:
        reasons.append("runtime_state_unknown")
        blockers.append("runtime_state_unknown")
    if unit_states is not None and set(unit_states) != EXPECTED_UNIT_KEYS:
        reasons.append("runtime_state_unknown")
        missing = sorted(EXPECTED_UNIT_KEYS - set(unit_states))
        extra = sorted(set(unit_states) - EXPECTED_UNIT_KEYS)
        blockers.extend(f"unit_state_missing:{name}" for name in missing)
        blockers.extend(f"unit_state_unknown:{name}" for name in extra)
    if bot_work is not None and set(bot_work) != EXPECTED_BOT_WORK_KEYS:
        reasons.append("runtime_state_unknown")
        missing = sorted(EXPECTED_BOT_WORK_KEYS - set(bot_work))
        extra = sorted(set(bot_work) - EXPECTED_BOT_WORK_KEYS)
        blockers.extend(f"bot_work_missing:{name}" for name in missing)
        blockers.extend(f"bot_work_unknown:{name}" for name in extra)

    in_flight: list[str] = []
    for unit, active in sorted((unit_states or {}).items()):
        # A polling bot being active is expected. Its explicit scheduled-work
        # marker, supplied through bot_work, is the in-flight signal.
        if active and unit not in PERSISTENT_BOT_UNITS:
            in_flight.append(f"unit:{unit}")
            reasons.append("unit_in_flight")
    for worker, active in sorted((bot_work or {}).items()):
        if active:
            in_flight.append(f"bot_work:{worker}")
            reasons.append("bot_work_in_flight")
    if queue_processing_count is not None:
        if queue_processing_count < 0:
            reasons.append("runtime_state_unknown")
            blockers.append("queue_processing_count_invalid")
        elif queue_processing_count:
            in_flight.append(f"queue_processing:{queue_processing_count}")
            reasons.append("queue_processing")
    if consumer_lock_held:
        in_flight.append("consumer_lock")
        reasons.append("consumer_lock_held")
    blockers.extend(in_flight)

    ordered_slots = tuple(sorted(next_slots, key=lambda item: (item.scheduled_at, item.route)))
    nearest = ordered_slots[0] if ordered_slots else None
    seconds: int | None = None
    valid_until: datetime | None = None
    if nearest is not None:
        seconds = max(0, int((nearest.scheduled_at - current).total_seconds()))
        valid_until = nearest.scheduled_at - timedelta(seconds=int(embargo_seconds))
        if seconds < int(embargo_seconds):
            reasons.append("natural_slot_embargo")
            blockers.append(f"natural_slot_embargo:{nearest.route}")
    for previous in previous_slots:
        elapsed = (current - previous.scheduled_at).total_seconds()
        if 0 < elapsed <= int(post_slot_claim_grace_seconds):
            reasons.append("post_slot_claim_grace")
            blockers.append(f"post_slot_claim_grace:{previous.route}")

    return DeployPreflight(
        allowed=not blockers,
        next_slots=ordered_slots,
        nearest_slot=nearest,
        seconds_to_nearest_slot=seconds,
        valid_until=valid_until,
        reason_codes=tuple(dict.fromkeys(reasons)),
        blockers=tuple(blockers),
        in_flight=tuple(in_flight),
    )


def load_schedule_snapshots(path: Path | str) -> dict[str, ScheduleSnapshot]:
    """Load a schedule-only snapshot; no environment file is ever inspected."""
    schedule_path = Path(path)
    if str(schedule_path) == "-":
        payload = json.load(sys.stdin)
    else:
        if schedule_path.is_symlink() or not schedule_path.is_file():
            raise RuntimeError("resolved schedule snapshot is unavailable")
        payload = json.loads(schedule_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != set(default_schedule_snapshots()):
        raise RuntimeError("resolved schedule snapshot has unknown routes")
    result: dict[str, ScheduleSnapshot] = {}
    for route, value in payload.items():
        if not isinstance(value, dict) or set(value) != {"daily_times", "weekly_times"}:
            raise RuntimeError(f"invalid resolved schedule: {route}")
        weekly = tuple(
            (tuple(int(day) for day in item[0]), str(item[1]))
            for item in value["weekly_times"]
        )
        result[route] = ScheduleSnapshot(
            daily_times=tuple(str(item) for item in value["daily_times"]),
            weekly_times=weekly,
        )
    return result


def _linux_process_identities(pid: int) -> frozenset[str]:
    """Return the exact identities emitted by Naz v2 and VOID v1."""
    try:
        stat = Path(f"/proc/{int(pid)}/stat").read_text(encoding="ascii")
        starttime = stat.rsplit(")", maxsplit=1)[1].split()[19]
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, IndexError, ValueError):
        return frozenset()
    identities = {f"linux:{starttime}"} if starttime else set()
    if boot_id and starttime:
        identities.add(f"linux:{boot_id}:{starttime}")
    return frozenset(identities)


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import os

        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _valid_marker_timestamp(value: object) -> bool:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None


def _naz_marker_lock_is_held(path: Path) -> bool:
    """Match Naz v2 ownership: only the active worker holds the marker flock."""
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("Naz marker lock inspection is unsupported") from exc
    try:
        with path.open("rb") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise RuntimeError("Naz marker lock state is unavailable") from exc
    return False


def _live_marker_work_keys(root: Path, persona: str) -> tuple[str, ...]:
    """Parse the two exact metadata-only marker schemas and map safe work keys."""
    if persona not in {"naz", "void"}:
        raise RuntimeError("scheduled-work marker persona is unknown")
    if not root.exists():
        if persona == "naz":
            raise RuntimeError("Naz scheduled-work marker directory is unavailable")
        return ()
    if root.is_symlink() or not root.is_dir():
        raise RuntimeError("scheduled-work marker directory is unsafe")
    if persona == "naz":
        paths = sorted(root.glob(".scheduled-work-*.json"))
    else:
        paths = sorted(root.glob("*.json"))
    work_keys: list[str] = []
    for marker in paths:
        if marker.is_symlink() or not marker.is_file():
            raise RuntimeError("scheduled-work marker is unsafe")
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("scheduled-work marker is unreadable") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("scheduled-work marker schema is invalid")

        if persona == "naz":
            expected_fields = {
                "schema", "label", "pid", "process_start_id", "started_at"
            }
            if set(payload) != expected_fields or payload.get("schema") != "naz_scheduled_work.v2":
                raise RuntimeError("Naz scheduled-work marker schema is invalid")
            label = str(payload.get("label") or "")
            work_key = NAZ_MARKER_LABEL_TO_WORK_KEY.get(label)
            expected_identity = str(payload.get("process_start_id") or "")
            token_is_valid = True
        else:
            expected_fields = {
                "schema", "kind", "token", "pid", "process_start_identity", "started_at"
            }
            if set(payload) != expected_fields or payload.get("schema") != "void_scheduled_work.v1":
                raise RuntimeError("VOID scheduled-work marker schema is invalid")
            kind = str(payload.get("kind") or "")
            work_key = VOID_MARKER_KIND_TO_WORK_KEY.get(kind)
            expected_identity = str(payload.get("process_start_identity") or "")
            token_is_valid = bool(
                re.fullmatch(r"[0-9a-f]{32}", str(payload.get("token") or ""))
            )

        pid = payload.get("pid")
        if (
            work_key is None
            or not token_is_valid
            or not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid <= 0
            or not expected_identity
            or not _valid_marker_timestamp(payload.get("started_at"))
        ):
            raise RuntimeError("scheduled-work marker metadata is invalid")
        if not _pid_is_alive(pid):
            continue
        identities = _linux_process_identities(pid)
        if not identities:
            raise RuntimeError("scheduled-work process identity is unavailable")
        if expected_identity in identities and (
            persona != "naz" or _naz_marker_lock_is_held(marker)
        ):
            work_keys.append(work_key)
        # A live reused PID with a different start identity is a stale marker.
    return tuple(work_keys)


def _consumer_lock_held(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("consumer lock state is unavailable")
    try:
        import fcntl
    except ImportError as exc:
        raise RuntimeError("consumer lock inspection is unsupported") from exc
    with path.open("rb") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return False


def collect_live_runtime_inputs(
    *,
    marker_dirs: Mapping[str, Path] = DEFAULT_MARKER_DIRS,
    queue_root: Path = DEFAULT_QUEUE_ROOT,
    consumer_lock: Path = DEFAULT_CONSUMER_LOCK,
) -> tuple[dict[str, bool], dict[str, bool], int, bool]:
    """Collect metadata-only live state; never read env, queue jobs or profiles."""
    units: dict[str, bool] = {}
    for unit in sorted(EXPECTED_UNIT_KEYS):
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        state = result.stdout.strip()
        if state in {"active", "activating", "deactivating"}:
            units[unit] = True
        elif state in {"inactive", "failed"}:
            units[unit] = False
        else:
            raise RuntimeError(f"unit state unavailable: {unit}")

    if set(marker_dirs) != {"naz", "void"}:
        raise RuntimeError("marker directory snapshot is incomplete")
    bot_work = {name: False for name in EXPECTED_BOT_WORK_KEYS}
    for persona, root in marker_dirs.items():
        for work_key in _live_marker_work_keys(Path(root), persona):
            if work_key not in bot_work or not work_key.startswith(f"{persona}."):
                raise RuntimeError("scheduled-work marker kind is unknown")
            bot_work[work_key] = True

    processing = queue_root / "processing"
    if processing.is_symlink() or not processing.is_dir():
        raise RuntimeError("queue processing state is unavailable")
    processing_count = 0
    for item in processing.iterdir():
        if item.is_symlink() or not item.is_dir():
            raise RuntimeError("queue processing entry is unsafe")
        processing_count += 1
    if consumer_lock.exists():
        lock_held = _consumer_lock_held(consumer_lock)
    elif units["void-vk-autopost.service"]:
        raise RuntimeError("active consumer has no inspectable lock")
    else:
        lock_held = False
    return units, bot_work, processing_count, lock_held


def _decision_json(decision: DeployPreflight) -> str:
    return json.dumps(
        {
            "allowed": decision.allowed,
            "next_slots": [
                {"route": item.route, "scheduled_at": item.scheduled_at.isoformat()}
                for item in decision.next_slots
            ],
            "nearest_slot": (
                None if decision.nearest_slot is None else {
                    "route": decision.nearest_slot.route,
                    "scheduled_at": decision.nearest_slot.scheduled_at.isoformat(),
                }
            ),
            "seconds_to_nearest_slot": decision.seconds_to_nearest_slot,
            "valid_until": decision.valid_until.isoformat() if decision.valid_until else None,
            "reason_codes": list(decision.reason_codes),
            "blockers": list(decision.blockers),
        },
        sort_keys=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only coordinated deploy preflight")
    parser.add_argument("--resolved-schedules", type=Path, required=True)
    args = parser.parse_args()
    schedules = load_schedule_snapshots(args.resolved_schedules)
    units, bot_work, processing, lock_held = collect_live_runtime_inputs()
    decision = assess_coordinated_deploy(
        datetime.now(MOSCOW_TZ),
        schedules=schedules,
        unit_states=units,
        bot_work=bot_work,
        queue_processing_count=processing,
        consumer_lock_held=lock_held,
    )
    print(_decision_json(decision))
    return 0 if decision.allowed else 75


if __name__ == "__main__":
    raise SystemExit(main())
