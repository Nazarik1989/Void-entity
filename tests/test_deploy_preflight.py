import tempfile
import unittest
import json
import os
from io import StringIO
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from unittest.mock import patch

import deploy_preflight
import main


MSK = ZoneInfo("Europe/Moscow")


def known_units(**changes):
    states = {name: False for name in deploy_preflight.EXPECTED_UNIT_KEYS}
    states.update(changes)
    return states


def known_bot_work(**changes):
    states = {name: False for name in deploy_preflight.EXPECTED_BOT_WORK_KEYS}
    states.update(changes)
    return states


def assess(now, *, schedules=None, units=None, bot_work=None, processing=0, lock=False):
    return deploy_preflight.assess_coordinated_deploy(
        now,
        schedules=schedules or deploy_preflight.default_schedule_snapshots(),
        unit_states=known_units(**(units or {})),
        bot_work=known_bot_work(**(bot_work or {})),
        queue_processing_count=processing,
        consumer_lock_held=lock,
    )


class DeployPreflightTests(unittest.TestCase):
    def test_telegram_slot_inside_fifteen_minutes_blocks_deploy(self):
        decision = assess(datetime(2026, 7, 22, 11, 50, tzinfo=MSK))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.nearest_slot.destination, "telegram")
        self.assertEqual(decision.seconds_to_nearest_slot, 600)

    def test_vk_slot_inside_fifteen_minutes_blocks_deploy(self):
        decision = assess(datetime(2026, 7, 22, 11, 50, 1, tzinfo=MSK))
        self.assertFalse(decision.allowed)
        vk_slot = next(slot for slot in decision.next_slots if slot.route == "void.vk")
        self.assertEqual(vk_slot.scheduled_at.strftime("%H:%M"), "12:00")

    def test_exactly_fifteen_minutes_is_allowed_when_no_unit_is_running(self):
        decision = assess(datetime(2026, 7, 22, 11, 45, tzinfo=MSK))
        self.assertTrue(decision.allowed)

    def test_telegram_poll_claim_grace_blocks_at_slot_plus_one_second(self):
        decision = assess(datetime(2026, 7, 22, 10, 0, 1, tzinfo=MSK))
        self.assertFalse(decision.allowed)
        self.assertIn("post_slot_claim_grace", decision.reason_codes)
        self.assertIn("post_slot_claim_grace:naz.telegram", decision.blockers)

    def test_systemd_accuracy_claim_grace_blocks_at_slot_plus_one_second(self):
        decision = assess(datetime(2026, 7, 22, 12, 0, 1, tzinfo=MSK))
        self.assertFalse(decision.allowed)
        self.assertIn("post_slot_claim_grace", decision.reason_codes)
        self.assertIn("post_slot_claim_grace:void.vk", decision.blockers)

    def test_in_flight_bot_or_producer_blocks_independently_of_distance(self):
        decision = assess(
            datetime(2026, 7, 22, 10, 0, tzinfo=MSK),
            units={"void-vk-producer.service": True},
        )
        self.assertFalse(decision.allowed)
        self.assertIn("unit:void-vk-producer.service", decision.blockers)

    def test_persistent_bot_active_alone_is_not_scheduled_work(self):
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=known_units(**{"naz-ai-bot.service": True, "void-entity.service": True}),
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertTrue(decision.allowed)
        self.assertNotIn("unit_in_flight", decision.reason_codes)

    def test_helper_and_systemd_have_exact_target_void_schedules(self):
        timer = Path("deploy/systemd/void-vk-producer.timer").read_text(encoding="utf-8")
        self.assertEqual(
            deploy_preflight.VOID_TELEGRAM_TIMES,
            ("12:00", "22:00"),
        )
        self.assertEqual(
            {
                line.strip()
                for line in timer.splitlines()
                if line.strip().startswith("OnCalendar=")
            },
            {
                "OnCalendar=*-*-* 12:00:00 Europe/Moscow",
                "OnCalendar=*-*-* 22:00:00 Europe/Moscow",
            },
        )
        self.assertIn("Persistent=false", timer)

    def test_canonical_evaluator_returns_all_four_routes_and_valid_until(self):
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=known_units(),
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertEqual({slot.route for slot in decision.next_slots}, {
            "naz.telegram", "naz.vk", "void.telegram", "void.vk",
        })
        self.assertEqual(decision.nearest_slot.route, "naz.telegram")
        self.assertEqual(decision.valid_until.strftime("%H:%M"), "09:45")

    def test_unknown_or_empty_schedule_fails_closed(self):
        schedules = deploy_preflight.default_schedule_snapshots()
        schedules["naz.telegram"] = None
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=schedules,
            unit_states=known_units(),
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("schedule_unknown", decision.reason_codes)
        self.assertIn("schedule_unknown:naz.telegram", decision.blockers)

        empty = deploy_preflight.default_schedule_snapshots()
        empty["naz.telegram"] = deploy_preflight.ScheduleSnapshot()
        empty_decision = assess(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK), schedules=empty
        )
        self.assertFalse(empty_decision.allowed)
        self.assertIn("schedule_unknown:naz.telegram", empty_decision.blockers)

    def test_runtime_snapshot_requires_exact_unit_and_marker_keys(self):
        missing_unit = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states={"void-entity.service": True},
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertFalse(missing_unit.allowed)
        self.assertTrue(any(item.startswith("unit_state_missing:") for item in missing_unit.blockers))

        extra_marker = known_bot_work()
        extra_marker["unknown.marker"] = False
        unknown_marker = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=known_units(),
            bot_work=extra_marker,
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertFalse(unknown_marker.allowed)
        self.assertIn("bot_work_unknown:unknown.marker", unknown_marker.blockers)

    def test_runtime_state_unknown_fails_closed(self):
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 9, 0, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=None,
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("runtime_state_unknown", decision.reason_codes)

    def test_moscow_midnight_rollover_uses_next_day_without_midnight_void_slot(self):
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 22, 23, 55, tzinfo=MSK),
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=known_units(),
            bot_work=known_bot_work(),
            queue_processing_count=0,
            consumer_lock_held=False,
        )
        void_slots = {
            slot.route: slot.scheduled_at.strftime("%Y-%m-%d %H:%M")
            for slot in decision.next_slots
            if slot.persona == "void"
        }
        self.assertEqual(
            void_slots,
            {
                "void.telegram": "2026-07-23 12:00",
                "void.vk": "2026-07-23 12:00",
            },
        )
        self.assertTrue(all(not value.endswith(" 00:00") for value in void_slots.values()))
        self.assertTrue(decision.allowed)

    def test_weekly_naz_vk_slot_and_every_in_flight_signal_block(self):
        decision = deploy_preflight.assess_coordinated_deploy(
            datetime(2026, 7, 26, 16, 20, tzinfo=MSK),  # Sunday.
            schedules=deploy_preflight.default_schedule_snapshots(),
            unit_states=known_units(**{"void-vk-producer.service": True}),
            bot_work=known_bot_work(**{"void.crosspost": True}),
            queue_processing_count=1,
            consumer_lock_held=True,
        )
        self.assertEqual(decision.nearest_slot.route, "naz.vk")
        self.assertEqual(decision.nearest_slot.scheduled_at.strftime("%H:%M"), "16:30")
        self.assertEqual(
            set(decision.reason_codes),
            {
                "unit_in_flight", "bot_work_in_flight", "queue_processing",
                "consumer_lock_held", "natural_slot_embargo",
            },
        )

    def test_resolved_void_snapshot_matches_runtime_and_tracked_timer(self):
        snapshot = main.resolved_void_schedule_snapshot()
        self.assertEqual(
            snapshot["void.telegram"]["daily_times"],
            main.VOID_TELEGRAM_AUTO_TIMES,
        )
        self.assertEqual(snapshot["void.vk"]["daily_times"], ("12:00", "22:00"))
        self.assertEqual(snapshot["void.vk"]["weekly_times"], ())

    def test_schedule_snapshot_loader_and_live_collector_never_read_env(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            schedules_path = root_path / "schedules.json"
            schedules_path.write_text(
                json.dumps(
                    {
                        route: {
                            "daily_times": list(snapshot.daily_times),
                            "weekly_times": [
                                [list(days), value] for days, value in snapshot.weekly_times
                            ],
                        }
                        for route, snapshot in deploy_preflight.default_schedule_snapshots().items()
                    }
                ),
                encoding="utf-8",
            )
            loaded = deploy_preflight.load_schedule_snapshots(schedules_path)
            self.assertEqual(set(loaded), set(deploy_preflight.default_schedule_snapshots()))

            queue_root = root_path / "queue"
            (queue_root / "processing").mkdir(parents=True)
            marker_dirs = {"naz": root_path / "naz-work", "void": root_path / "void-work"}
            marker_dirs["naz"].mkdir()
            marker_dirs["void"].mkdir()

            def unit_state(command, **kwargs):
                active = command[-1] in deploy_preflight.PERSISTENT_BOT_UNITS
                return SimpleNamespace(stdout="active\n" if active else "inactive\n")

            with patch("deploy_preflight.subprocess.run", side_effect=unit_state):
                units, bot_work, processing, lock_held = deploy_preflight.collect_live_runtime_inputs(
                    marker_dirs=marker_dirs,
                    queue_root=queue_root,
                    consumer_lock=root_path / "missing.lock",
                )
            self.assertEqual(set(units), deploy_preflight.EXPECTED_UNIT_KEYS)
            self.assertEqual(set(bot_work), deploy_preflight.EXPECTED_BOT_WORK_KEYS)
            self.assertEqual(processing, 0)
            self.assertFalse(lock_held)

        collector_source = Path(deploy_preflight.__file__).read_text(encoding="utf-8")
        self.assertNotIn("os.environ", collector_source)
        self.assertNotIn("dotenv", collector_source.casefold())

    def test_schedule_snapshot_loader_accepts_ephemeral_stdin(self):
        rendered = json.dumps(
            {
                route: {
                    "daily_times": list(snapshot.daily_times),
                    "weekly_times": [
                        [list(days), value] for days, value in snapshot.weekly_times
                    ],
                }
                for route, snapshot in deploy_preflight.default_schedule_snapshots().items()
            }
        )
        with patch.object(deploy_preflight.sys, "stdin", StringIO(rendered)):
            loaded = deploy_preflight.load_schedule_snapshots("-")
        self.assertEqual(set(loaded), set(deploy_preflight.default_schedule_snapshots()))

    def test_live_collector_maps_every_actual_naz_v2_marker_label(self):
        with tempfile.TemporaryDirectory() as root:
            marker_root = Path(root)
            for index, label in enumerate(deploy_preflight.NAZ_MARKER_LABEL_TO_WORK_KEY):
                (marker_root / f".scheduled-work-{label}.4242.{index}.json").write_text(
                    json.dumps(
                        {
                            "schema": "naz_scheduled_work.v2",
                            "label": label,
                            "pid": 4242,
                            "process_start_id": "linux:987654",
                            "started_at": "2026-07-22T10:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            with (
                patch.object(deploy_preflight, "_pid_is_alive", return_value=True),
                patch.object(
                    deploy_preflight,
                    "_linux_process_identities",
                    return_value=frozenset({"linux:987654"}),
                ),
                patch.object(
                    deploy_preflight, "_naz_marker_lock_is_held", return_value=True
                ),
            ):
                work_keys = deploy_preflight._live_marker_work_keys(marker_root, "naz")
        self.assertEqual(
            set(work_keys), set(deploy_preflight.NAZ_MARKER_LABEL_TO_WORK_KEY.values())
        )
        self.assertEqual(
            deploy_preflight.DEFAULT_MARKER_DIRS["naz"], Path("/var/lib/naz-ai-bot")
        )

    def test_unlocked_naz_marker_from_live_pid_is_stale(self):
        with tempfile.TemporaryDirectory() as root:
            marker_root = Path(root)
            marker = marker_root / ".scheduled-work-telegram_autopost.4242.stale.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema": "naz_scheduled_work.v2",
                        "label": "telegram_autopost",
                        "pid": 4242,
                        "process_start_id": "linux:987654",
                        "started_at": "2026-07-22T10:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(deploy_preflight, "_pid_is_alive", return_value=True),
                patch.object(
                    deploy_preflight,
                    "_linux_process_identities",
                    return_value=frozenset({"linux:987654"}),
                ),
                patch.object(
                    deploy_preflight, "_naz_marker_lock_is_held", return_value=False
                ),
            ):
                self.assertEqual(
                    deploy_preflight._live_marker_work_keys(marker_root, "naz"), ()
                )

    def test_unknown_naz_v2_marker_label_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            marker_root = Path(root)
            marker = marker_root / ".scheduled-work-unknown.4242.dead.json"
            marker.write_text(
                json.dumps(
                    {
                        "schema": "naz_scheduled_work.v2",
                        "label": "unknown",
                        "pid": 4242,
                        "process_start_id": "linux:987654",
                        "started_at": "2026-07-22T10:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "metadata is invalid"):
                deploy_preflight._live_marker_work_keys(marker_root, "naz")

    def test_scheduled_work_marker_acquires_reports_and_releases(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            main, "VOID_SCHEDULED_WORK_DIR", Path(root) / "work"
        ):
            self.assertFalse(main.scheduled_work_snapshot()["active"])
            with main.scheduled_work_marker("void.telegram") as marker:
                self.assertTrue(marker.is_file())
                payload = json.loads(marker.read_text(encoding="utf-8"))
                self.assertEqual(payload["pid"], os.getpid())
                self.assertEqual(
                    payload["process_start_identity"],
                    main.process_start_identity(os.getpid()),
                )
                snapshot = main.scheduled_work_snapshot()
                self.assertTrue(snapshot["active"])
                self.assertEqual(snapshot["kinds"], ("void.telegram",))
            self.assertFalse(main.scheduled_work_snapshot()["active"])

    def test_stale_or_reused_pid_marker_does_not_stay_active_forever(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            main, "VOID_SCHEDULED_WORK_DIR", Path(root) / "work"
        ):
            main.VOID_SCHEDULED_WORK_DIR.mkdir()
            stale = main.VOID_SCHEDULED_WORK_DIR / "stale.json"
            stale.write_text(
                json.dumps(
                    {
                        "schema": "void_scheduled_work.v1",
                        "kind": "void.telegram",
                        "token": "stale",
                        "pid": os.getpid(),
                        "process_start_identity": "different-process-start",
                    }
                ),
                encoding="utf-8",
            )
            snapshot = main.scheduled_work_snapshot()
            self.assertFalse(snapshot["active"])
            self.assertEqual(snapshot["stale_count"], 1)

    def test_scheduled_work_marker_releases_on_exception(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            main, "VOID_SCHEDULED_WORK_DIR", Path(root) / "work"
        ):
            with self.assertRaisesRegex(RuntimeError, "callback failed"):
                with main.scheduled_work_marker("void.crosspost"):
                    raise RuntimeError("callback failed")
            self.assertFalse(main.scheduled_work_snapshot()["active"])


if __name__ == "__main__":
    unittest.main()
