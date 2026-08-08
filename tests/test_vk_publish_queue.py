import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import vk_browser_publisher
from main import get_draft, init_db, save_draft
from vk_browser_publisher import parse_scheduled_draft_id
from vk_publish_queue import (
    DuplicateJobError,
    DuplicateTrackError,
    MAX_RETRY_ATTEMPTS,
    QueueValidationError,
    RETRYABLE_EXIT_CODE,
    RETRY_STATE_FILENAME,
    RetryablePublishError,
    TRACK_HISTORY_BACKFILL_MARKER,
    TRACK_HISTORY_CHECKPOINT_SCHEMA,
    _backfill_full_track_history,
    _replace_job_track_query,
    build_job,
    canonical_job_id,
    consume_once,
    enqueue_job,
    publication_receipts,
    recent_track_keys,
    requeue_failed,
    unavailable_track_keys,
    validate_job,
)
from vk_queue_consumer import (
    AUTH_SELECTORS,
    AUDIO_PICKER_TRIGGER_SELECTORS,
    ATTACHED_AUDIO_SELECTORS,
    COMPOSER_INPUT_SELECTORS,
    COMPOSER_TRIGGER_SELECTORS,
    PUBLICATION_ATTEMPT_FILENAME,
    PUBLISHED_AUDIO_SELECTORS,
    VkAuthenticationRequiredError,
    VkComposerStructureError,
    VkPublishConfirmationError,
    _PublicationEvidence,
    _audio_identity_matches,
    _audio_dom_diagnostics,
    _audio_row_score,
    _audio_search_is_file_picker,
    _audio_title_fallback,
    _audio_trigger_diagnostics,
    _attach_track,
    _authentication_required,
    _click_first_text,
    _clear_saved_composer_attachments,
    _composer_image_file_input,
    _confirm_track_attached,
    _inspect_unresolved_publication,
    _locator_or_ancestor_audio_matches,
    _load_publication_attempt,
    _open_audio_picker,
    _open_composer,
    _open_composer_once,
    _publication_cooldown_remaining,
    _publish_and_confirm,
    _published_post_evidence,
    _record_admin_notice,
    _record_publication_attempt,
    _reconcile_confirmed_unresolved,
    _unresolved_publication_attempt,
    _wait_for_publication_confirmation,
    consume_queue,
)
from void_vk_producer import sync_published_drafts


class VkPublishQueueTests(unittest.TestCase):
    group = "237593988"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def job(self, **changes):
        values = dict(producer="void", target_group_id=self.group, text="post", media=["image-1.png"], track_query="Faithless Sobersoul", dedupe_key="draft:296", source_ref="void:draft:296")
        values.update(changes)
        return build_job(**values)

    def enqueue(self, job=None):
        return enqueue_job(self.root, job or self.job(), {"image-1.png": b"png"})

    def write_receipt(self, job, published_at="2026-07-30T12:00:00Z"):
        receipts = self.root / "published"
        receipts.mkdir(exist_ok=True)
        payload = {
            "schema": "vk_publication_receipt.v1",
            "job_id": job["job_id"],
            "producer": job["producer"],
            "source_ref": job["source_ref"],
            "published_at": published_at,
        }
        (receipts / f"{job['job_id']}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def test_parse_scheduled_draft_id_regression(self):
        self.assertEqual(parse_scheduled_draft_id("Scheduled VK draft: #296"), 296)

    def test_legacy_remote_scheduled_publisher_is_disabled(self):
        with (
            patch("vk_browser_publisher.VK_VPS_DB_SCP", "void@example:/srv/void.db"),
            patch("vk_browser_publisher.make_remote_scheduled_draft") as make_remote,
            self.assertRaisesRegex(RuntimeError, "VPS producer timer"),
        ):
            vk_browser_publisher.publish_scheduled()
        make_remote.assert_not_called()

    def test_legacy_publish_draft_enqueues_instead_of_bypassing_history(self):
        queued = self.root / "pending" / "void-job"
        with (
            patch("vk_browser_publisher.VK_VPS_DB_SCP", ""),
            patch("vk_browser_publisher.sync_db_from_vps") as sync,
            patch("vk_browser_publisher.enqueue_draft", return_value=queued) as enqueue,
            patch("vk_browser_publisher.open_payload") as direct_publish,
        ):
            vk_browser_publisher.publish_draft(42)

        sync.assert_called_once()
        enqueue.assert_called_once_with(42)
        direct_publish.assert_not_called()

    def test_open_payload_rejects_direct_publish_before_opening_browser(self):
        with (
            patch("vk_browser_publisher.ensure_playwright") as ensure_browser,
            self.assertRaisesRegex(RuntimeError, "canonical VK queue consumer"),
        ):
            vk_browser_publisher.open_payload("missing.json", publish=True)

        ensure_browser.assert_not_called()

    def test_open_payload_rejects_prepared_composer_preview(self):
        with (
            patch("vk_browser_publisher.ensure_playwright") as ensure_browser,
            self.assertRaisesRegex(RuntimeError, "composer previews are disabled"),
        ):
            vk_browser_publisher.open_payload("missing.json")

        ensure_browser.assert_not_called()

    def test_open_payload_cli_routes_to_the_fail_closed_handler(self):
        with (
            patch.object(
                sys,
                "argv",
                ["vk_browser_publisher.py", "open-payload", "payload.json"],
            ),
            patch("vk_browser_publisher.open_payload") as open_payload,
        ):
            vk_browser_publisher.main_cli()

        open_payload.assert_called_once_with("payload.json")

    def test_open_payload_cli_rejects_the_removed_publish_flag(self):
        with (
            patch.object(
                sys,
                "argv",
                [
                    "vk_browser_publisher.py",
                    "open-payload",
                    "payload.json",
                    "--publish",
                ],
            ),
            patch.object(sys, "stderr"),
            patch("vk_browser_publisher.open_payload") as open_payload,
            self.assertRaises(SystemExit) as raised,
        ):
            vk_browser_publisher.main_cli()

        self.assertEqual(raised.exception.code, 2)
        open_payload.assert_not_called()

    def test_legacy_browser_consumer_is_disabled_before_queue_access(self):
        with (
            patch("vk_browser_publisher.publish_queue_job") as legacy_publish,
            self.assertRaisesRegex(RuntimeError, "vk_queue_consumer.py consume-queue"),
        ):
            vk_browser_publisher.consume_queue()

        legacy_publish.assert_not_called()

    def test_legacy_browser_publish_callback_is_disabled(self):
        with (
            patch("vk_browser_publisher.open_payload") as direct_publish,
            self.assertRaisesRegex(RuntimeError, "legacy browser queue callback"),
        ):
            vk_browser_publisher.publish_queue_job(self.job(), [])

        direct_publish.assert_not_called()

    def test_valid_job(self):
        self.assertEqual(validate_job(self.enqueue(), self.group)["producer"], "void")

    def test_every_job_requires_a_music_track(self):
        with self.assertRaisesRegex(QueueValidationError, "track_query is required"):
            self.job(track_query="")

    def test_void_enqueue_rejects_track_outside_current_catalog(self):
        catalog = frozenset({"catalog artist catalog title"})
        off_catalog = self.job(
            dedupe_key="void-off-catalog",
            track_query="Unknown Artist Unknown Title",
        )

        with (
            patch("vk_publish_queue._void_track_catalog_keys", return_value=catalog),
            self.assertRaisesRegex(QueueValidationError, "current track catalog"),
        ):
            self.enqueue(off_catalog)

        self.assertFalse((self.root / "pending").exists())

    def test_naz_track_does_not_use_the_void_catalog_allowlist(self):
        job = self.job(
            producer="naz",
            dedupe_key="naz-off-void-catalog",
            track_query="Naz Artist Naz Title",
            source_ref="schedule:2026-07-30:12:00",
        )

        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            side_effect=AssertionError("Naz must not load the VOID catalog"),
        ):
            queued = self.enqueue(job)

        self.assertTrue(queued.is_dir())

    def test_consumer_rejects_manually_queued_void_track_outside_catalog(self):
        job = self.job(
            dedupe_key="manual-void-off-catalog",
            track_query="Unknown Artist Unknown Title",
        )
        pending = self.root / "pending" / job["job_id"]
        pending.mkdir(parents=True)
        (pending / "image-1.png").write_bytes(b"png")
        (pending / "job.json").write_text(
            json.dumps(job, ensure_ascii=False),
            encoding="utf-8",
        )
        publish = Mock()

        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"catalog artist catalog title"}),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 1)

        publish.assert_not_called()
        failed = self.root / "failed" / job["job_id"]
        self.assertTrue(failed.is_dir())
        self.assertIn(
            "current track catalog",
            (failed / "error.txt").read_text(encoding="utf-8"),
        )

    def test_shared_history_keeps_full_lru_with_legacy_last_eight_view(self):
        void_catalog = frozenset(f"artist track {index}" for index in (1, 3, 5, 7))
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=void_catalog,
        ):
            for index in range(8):
                producer = "naz" if index % 2 == 0 else "void"
                job = self.job(
                    producer=producer,
                    dedupe_key=f"shared-{index}",
                    track_query=f"Artist Track {index}",
                )
                self.enqueue(job)
                publish = Mock()
                self.assertEqual(consume_once(self.root, self.group, publish), 0)
                publish.assert_called_once()

            self.assertEqual(len(recent_track_keys(self.root)), 8)
            repeated = self.job(
                producer="naz",
                dedupe_key="shared-repeat",
                track_query="artist track 0",
            )
            with self.assertRaisesRegex(DuplicateTrackError, "last 8"):
                self.enqueue(repeated)

            ninth = self.job(
                producer="naz",
                dedupe_key="shared-ninth",
                track_query="Artist Track 8",
            )
            self.enqueue(ninth)
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)
        self.assertNotIn("artist track 0", recent_track_keys(self.root))
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            [f"artist track {index}" for index in range(9)],
        )

    def test_consumer_enforces_the_full_rotation_against_stale_jobs(self):
        catalog = frozenset(f"rotation track {index}" for index in range(4))
        with patch("vk_publish_queue._void_track_catalog_keys", return_value=catalog):
            for index in range(3):
                job = self.job(
                    dedupe_key=f"rotation-{index}",
                    track_query=f"Rotation Track {index}",
                )
                self.enqueue(job)
                self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

            early_repeat = self.job(
                dedupe_key="rotation-early-repeat",
                track_query="Rotation Track 0",
            )
            with self.assertRaisesRegex(DuplicateTrackError, "other 3"):
                self.enqueue(early_repeat)

            fourth = self.job(
                dedupe_key="rotation-3",
                track_query="Rotation Track 3",
            )
            self.enqueue(fourth)
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

            allowed_repeat = self.job(
                dedupe_key="rotation-allowed-repeat",
                track_query="Rotation Track 0",
            )
            self.enqueue(allowed_repeat)
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            [
                "rotation track 1",
                "rotation track 2",
                "rotation track 3",
                "rotation track 0",
            ],
        )

    def test_consumer_rejects_a_duplicate_queued_before_the_first_publish(self):
        catalog = frozenset(
            {
                "stale rotation track",
                "unused rotation track 1",
                "unused rotation track 2",
                "unused rotation track 3",
            }
        )
        with patch("vk_publish_queue._void_track_catalog_keys", return_value=catalog):
            first = self.job(
                dedupe_key="stale-first",
                track_query="Stale Rotation Track",
            )
            stale = self.job(
                dedupe_key="stale-second",
                track_query="Stale Rotation Track",
            )
            self.enqueue(first)
            self.enqueue(stale)

            first_publish = Mock()
            self.assertEqual(consume_once(self.root, self.group, first_publish), 0)
            first_publish.assert_called_once()

            duplicate_publish = Mock()
            self.assertEqual(consume_once(self.root, self.group, duplicate_publish), 1)
            duplicate_publish.assert_not_called()

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["stale rotation track"],
        )

    def test_naz_keeps_its_independent_last_eight_rotation(self):
        for index in range(9):
            job = self.job(
                producer="naz",
                dedupe_key=f"naz-rotation-{index}",
                track_query=f"Naz Track {index}",
            )
            self.enqueue(job)
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

        repeated = self.job(
            producer="naz",
            dedupe_key="naz-rotation-repeat",
            track_query="Naz Track 0",
        )
        self.enqueue(repeated)
        publish = Mock()
        self.assertEqual(consume_once(self.root, self.group, publish), 0)
        publish.assert_called_once()

    def test_void_rotation_fails_closed_when_catalog_size_drifts(self):
        catalog_path = self.root / "tracks.json"
        catalog_path.write_text(
            json.dumps(
                {
                    "tracks": [
                        {"artist": "A", "title": "One"},
                        {"artist": "B", "title": "Two"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        job = self.job(
            dedupe_key="catalog-size-drift",
            track_query="A One",
        )
        with (
            patch("vk_publish_queue.VK_MUSIC_TRACKS_FILE", catalog_path),
            patch("vk_publish_queue.TRACK_ROTATION_SIZE", 3),
            self.assertRaisesRegex(QueueValidationError, "catalog size"),
        ):
            self.enqueue(job)

    def test_naz_tracks_do_not_advance_the_void_catalog_cycle(self):
        catalog = frozenset(f"void catalog {index}" for index in range(4))
        with patch("vk_publish_queue._void_track_catalog_keys", return_value=catalog):
            first = self.job(
                dedupe_key="void-catalog-first",
                track_query="Void Catalog 0",
            )
            self.enqueue(first)
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

            for index in range(4):
                naz = self.job(
                    producer="naz",
                    dedupe_key=f"naz-between-void-{index}",
                    track_query=f"Naz Independent {index}",
                )
                self.enqueue(naz)
                self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

            repeated = self.job(
                dedupe_key="void-catalog-too-early",
                track_query="Void Catalog 0",
            )
            with self.assertRaisesRegex(DuplicateTrackError, "other 3"):
                self.enqueue(repeated)

    def test_legacy_history_is_backfilled_from_confirmed_done_jobs(self):
        existing = ["legacy oldest", "legacy newest"]
        (self.root / "recent-tracks.json").write_text(
            json.dumps({"tracks": [{"key": key} for key in existing]}),
            encoding="utf-8",
        )
        expected = list(existing)
        (self.root / "done").mkdir()
        void_catalog = frozenset(
            f"backfill track {index}" for index in range(1, 12, 2)
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=void_catalog,
        ):
            for index in range(12):
                job = self.job(
                    producer="naz" if index % 2 == 0 else "void",
                    dedupe_key=f"backfill-{index}",
                    track_query=f"Backfill Track {index}",
                )
                directory = self.enqueue(job)
                os.replace(directory, self.root / "done" / directory.name)
                expected.append(f"backfill track {index}")

        self.assertEqual(consume_once(self.root, self.group, Mock()), 0)
        history = recent_track_keys(self.root, limit=None)
        self.assertEqual(history[-len(existing) :], existing)
        self.assertEqual(set(history), set(expected))
        self.assertEqual(len(history), len(expected))
        self.assertEqual(len(publication_receipts(self.root)), 12)

    def test_track_history_backfill_resolves_confirmed_processing_job(self):
        job = self.job(
            dedupe_key="processing-receipt",
            track_query="Processing Track",
            source_ref="void:draft:processing",
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"processing track"}),
        ):
            directory = self.enqueue(job)
        processing = self.root / "processing"
        processing.mkdir()
        os.replace(directory, processing / directory.name)
        self.write_receipt(job)

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(recent_track_keys(self.root, limit=None), ["processing track"])
        self.assertTrue((self.root / TRACK_HISTORY_BACKFILL_MARKER).is_file())

    def test_track_history_backfill_resolves_published_failed_job(self):
        job = self.job(
            dedupe_key="failed-after-receipt",
            track_query="Published Failed Track",
            source_ref="void:draft:failed-after-receipt",
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"published failed track"}),
        ):
            directory = self.enqueue(job)
        failed = self.root / "failed"
        failed.mkdir()
        os.replace(directory, failed / directory.name)
        self.write_receipt(job)

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["published failed track"],
        )
        self.assertTrue((self.root / TRACK_HISTORY_BACKFILL_MARKER).is_file())

    def test_track_history_backfill_accepts_receipted_legacy_metadata_job(self):
        job = self.job(
            dedupe_key="legacy-metadata-receipt",
            track_query="Legacy Metadata Track",
            source_ref="void:draft:legacy-metadata",
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"legacy metadata track"}),
        ):
            directory = self.enqueue(job)
        job_path = directory / "job.json"
        legacy = json.loads(job_path.read_text(encoding="utf-8"))
        legacy["schema"] = "vk_publish_job.v2"
        legacy["metadata"] = {"legacy": True}
        job_path.write_text(json.dumps(legacy), encoding="utf-8")
        with self.assertRaises(QueueValidationError):
            validate_job(directory, self.group)
        done = self.root / "done"
        done.mkdir()
        os.replace(directory, done / directory.name)
        self.write_receipt(job)

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["legacy metadata track"],
        )
        self.assertTrue((self.root / TRACK_HISTORY_BACKFILL_MARKER).is_file())

    def test_backfill_never_reorders_authoritative_legacy_recent_suffix(self):
        recent = [f"recent track {index}" for index in range(8)]
        (self.root / "done").mkdir()
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset(recent),
        ):
            for position, index in enumerate(reversed(range(8))):
                job = self.job(
                    dedupe_key=f"recent-suffix-{index}",
                    track_query=f"Recent Track {index}",
                    source_ref=f"void:draft:recent-{index}",
                )
                directory = self.enqueue(job)
                os.replace(directory, self.root / "done" / directory.name)
                self.write_receipt(
                    job,
                    published_at=f"2026-07-30T12:{position:02d}:00Z",
                )
        (self.root / "recent-tracks.json").write_text(
            json.dumps({"tracks": [{"key": key} for key in recent]}),
            encoding="utf-8",
        )

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(recent_track_keys(self.root, limit=None), recent)

    def test_unresolved_receipt_does_not_write_track_state_or_marker(self):
        existing_payload = {"tracks": [{"key": "existing track"}]}
        history_path = self.root / "recent-tracks.json"
        history_path.write_text(json.dumps(existing_payload), encoding="utf-8")
        job = self.job(
            dedupe_key="missing-receipt-job",
            track_query="Missing Track",
            source_ref="void:draft:missing",
        )
        self.write_receipt(job)
        original = history_path.read_bytes()

        with self.assertRaisesRegex(
            QueueValidationError,
            "confirmed VK publication job is unavailable",
        ):
            _backfill_full_track_history(self.root, self.group)

        self.assertEqual(history_path.read_bytes(), original)
        self.assertFalse((self.root / TRACK_HISTORY_BACKFILL_MARKER).exists())

    def test_invalid_track_history_marker_fails_before_state_write(self):
        history_path = self.root / "recent-tracks.json"
        history_path.write_text(
            json.dumps({"tracks": [{"key": "existing track"}]}),
            encoding="utf-8",
        )
        marker = self.root / TRACK_HISTORY_BACKFILL_MARKER
        marker.mkdir()
        original = history_path.read_bytes()

        with self.assertRaisesRegex(QueueValidationError, "marker is invalid"):
            _backfill_full_track_history(self.root, self.group)

        self.assertEqual(history_path.read_bytes(), original)

    def test_checkpoint_repairs_receipt_written_before_history_crash(self):
        _backfill_full_track_history(self.root, self.group)
        job = self.job(
            dedupe_key="receipt-before-history-crash",
            track_query="Recovered Published Track",
            source_ref="void:draft:receipt-before-history-crash",
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"recovered published track"}),
        ):
            directory = self.enqueue(job)
        failed = self.root / "failed"
        failed.mkdir()
        os.replace(directory, failed / directory.name)
        self.write_receipt(job)

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["recovered published track"],
        )
        checkpoint = json.loads(
            (self.root / TRACK_HISTORY_BACKFILL_MARKER).read_text(encoding="utf-8")
        )
        self.assertEqual(checkpoint["schema"], TRACK_HISTORY_CHECKPOINT_SCHEMA)
        self.assertIn(job["job_id"], checkpoint["receipt_job_ids"])
        self.assertEqual(checkpoint["track_keys"], ["recovered published track"])

    def test_checkpoint_restores_full_lru_after_legacy_consumer_truncation(self):
        done = self.root / "done"
        done.mkdir()
        jobs = []
        catalog = frozenset({f"rollback track {index}" for index in range(13)})
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            for index in range(13):
                job = self.job(
                    producer="naz" if index % 2 == 0 else "void",
                    dedupe_key=f"rollback-history-{index}",
                    track_query=f"Rollback Track {index}",
                    source_ref=f"void:draft:rollback-{index}",
                )
                directory = self.enqueue(job)
                os.replace(directory, done / directory.name)
                jobs.append(job)

        for index, job in enumerate(jobs[:12]):
            self.write_receipt(
                job,
                published_at=f"2026-07-30T12:{index:02d}:00Z",
            )
        _backfill_full_track_history(self.root, self.group)
        expected_before = [f"rollback track {index}" for index in range(12)]
        self.assertEqual(recent_track_keys(self.root, limit=None), expected_before)

        # The pre-full-history consumer retained only its last-eight view.
        self.write_receipt(jobs[12], published_at="2026-07-30T12:12:00Z")
        legacy_view = [*expected_before[-7:], "rollback track 12"]
        (self.root / "recent-tracks.json").write_text(
            json.dumps({"tracks": [{"key": key} for key in legacy_view]}),
            encoding="utf-8",
        )

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            [f"rollback track {index}" for index in range(13)],
        )

    def test_checkpoint_restores_missing_history_without_new_receipts(self):
        done = self.root / "done"
        done.mkdir()
        job = self.job(
            dedupe_key="missing-history-restore",
            track_query="Durable History Track",
            source_ref="void:draft:missing-history-restore",
        )
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=frozenset({"durable history track"}),
        ):
            directory = self.enqueue(job)
        os.replace(directory, done / directory.name)
        self.write_receipt(job)
        _backfill_full_track_history(self.root, self.group)
        (self.root / "recent-tracks.json").unlink()

        _backfill_full_track_history(self.root, self.group)

        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["durable history track"],
        )

    def test_missing_vk_track_is_replaced_for_the_next_safe_timer(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
            ("third artist third track", "Third Artist Third Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            directory = self.enqueue(self.job(dedupe_key="retry-track"))
            publish = Mock(
                side_effect=RetryablePublishError("no matching VK audio result")
            )

            self.assertEqual(
                consume_once(self.root, self.group, publish),
                0,
            )

            pending = self.root / "pending" / directory.name
            self.assertTrue(pending.is_dir())
            self.assertFalse((pending / "retry.txt").exists())
            self.assertFalse((pending / RETRY_STATE_FILENAME).exists())
            self.assertEqual(
                validate_job(pending, self.group)["track_query"],
                "Next Artist Next Track",
            )
            self.assertFalse((self.root / "done" / directory.name).exists())
            self.assertFalse((self.root / "failed" / directory.name).exists())
            self.assertEqual(publication_receipts(self.root), [])
            self.assertEqual(recent_track_keys(self.root, limit=None), [])

            publish.reset_mock()
            publish.side_effect = None
            self.assertEqual(
                consume_once(self.root, self.group, publish),
                0,
            )
        published_job = publish.call_args.args[0]
        self.assertNotIn("_skip_audio", published_job)
        self.assertEqual(published_job["track_query"], "Next Artist Next Track")
        self.assertTrue((self.root / "done" / directory.name).is_dir())
        self.assertEqual(len(publication_receipts(self.root)), 1)
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )
        self.assertEqual(
            unavailable_track_keys(self.root),
            ["faithless sobersoul"],
        )

    def test_failed_atomic_track_replacement_preserves_original_job(self):
        directory = self.enqueue(
            self.job(dedupe_key="atomic-track-replacement")
        )

        with (
            patch(
                "vk_publish_queue.os.replace",
                side_effect=OSError("simulated atomic rename failure"),
            ),
            self.assertRaisesRegex(OSError, "atomic rename failure"),
        ):
            _replace_job_track_query(
                directory,
                self.group,
                "Next Artist Next Track",
            )

        self.assertEqual(
            validate_job(directory, self.group)["track_query"],
            "Faithless Sobersoul",
        )
        self.assertEqual(
            list(directory.glob(".job.json.track-*")),
            [],
        )

    def test_crash_after_atomic_replacement_discards_stale_audio_backoff(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            directory = self.enqueue(
                self.job(dedupe_key="replacement-before-marker-cleanup")
            )
        (self.root / "unavailable-tracks.json").write_text(
            json.dumps({"tracks": [{"key": "faithless sobersoul"}]}),
            encoding="utf-8",
        )
        timestamp = "2026-08-01T12:00:00Z"
        (directory / RETRY_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "schema": "vk_publish_retry.v2",
                    "attempts": 1,
                    "error_code": "vk_audio_no_match",
                    "unavailable_track_queries": ["faithless sobersoul"],
                    "first_failed_at": timestamp,
                    "last_failed_at": timestamp,
                }
            ),
            encoding="utf-8",
        )
        (directory / "retry.txt").write_text("stale", encoding="utf-8")
        _replace_job_track_query(
            directory,
            self.group,
            "Next Artist Next Track",
        )
        publish = Mock()

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        self.assertEqual(
            publish.call_args.args[0]["track_query"],
            "Next Artist Next Track",
        )
        done = self.root / "done" / directory.name
        self.assertFalse((done / RETRY_STATE_FILENAME).exists())
        self.assertFalse((done / "retry.txt").exists())
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )

    def test_replacement_track_survives_a_later_composer_failure(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
            ("third artist third track", "Third Artist Third Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        calls = 0

        def publish(job, _media):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RetryablePublishError("no matching VK audio result")
            self.assertNotIn("_skip_audio", job)
            self.assertEqual(job["track_query"], "Next Artist Next Track")
            if calls == 2:
                raise RetryablePublishError("composer temporarily unavailable")

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            directory = self.enqueue(
                self.job(dedupe_key="durable-audio-replacement")
            )
            self.assertEqual(
                consume_once(self.root, self.group, publish), 0
            )
            self.assertEqual(
                consume_once(self.root, self.group, publish), RETRYABLE_EXIT_CODE
            )
            pending = self.root / "pending" / directory.name
            self.assertEqual(
                validate_job(pending, self.group)["track_query"],
                "Next Artist Next Track",
            )
            retry_state = json.loads(
                (pending / RETRY_STATE_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(
                retry_state["unavailable_track_queries"],
                [],
            )
            self.assertEqual(
                retry_state["error_code"],
                "vk_composer_unavailable",
            )
            self.assertEqual(consume_once(self.root, self.group, publish), 0)
            self.assertEqual(calls, 2)
            os.utime(pending / "retry.txt", (0, 0))
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        state = json.loads(
            (self.root / "done" / directory.name / "job.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(calls, 3)
        self.assertEqual(state["track_query"], "Next Artist Next Track")
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )

    def test_missing_track_promotion_wins_over_retry_exhaustion(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            directory = self.enqueue(
                self.job(dedupe_key="missing-at-retry-limit")
            )
        timestamp = "2026-08-01T12:00:00Z"
        (directory / RETRY_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "schema": "vk_publish_retry.v2",
                    "attempts": MAX_RETRY_ATTEMPTS - 1,
                    "error_code": "vk_composer_unavailable",
                    "unavailable_track_queries": [],
                    "first_failed_at": timestamp,
                    "last_failed_at": timestamp,
                }
            ),
            encoding="utf-8",
        )
        retry_file = directory / "retry.txt"
        retry_file.write_text("old retry", encoding="utf-8")
        os.utime(retry_file, (0, 0))
        publish = Mock(
            side_effect=RetryablePublishError("no matching VK audio result")
        )

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        pending = self.root / "pending" / directory.name
        self.assertTrue(pending.is_dir())
        self.assertFalse((self.root / "failed" / directory.name).exists())
        self.assertFalse((pending / RETRY_STATE_FILENAME).exists())
        self.assertEqual(
            validate_job(pending, self.group)["track_query"],
            "Next Artist Next Track",
        )

    def test_legacy_audio_retry_state_migrates_to_durable_replacement(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            directory = self.enqueue(
                self.job(dedupe_key="legacy-audio-replacement")
            )
        timestamp = "2026-08-01T12:00:00Z"
        (directory / RETRY_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "schema": "vk_publish_retry.v1",
                    "attempts": 10,
                    "error_code": "vk_audio_no_match",
                    "first_failed_at": timestamp,
                    "last_failed_at": timestamp,
                }
            ),
            encoding="utf-8",
        )
        (directory / "retry.txt").write_text("legacy", encoding="utf-8")
        publish = Mock()

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 0)
            publish.assert_not_called()
            pending = self.root / "pending" / directory.name
            self.assertEqual(
                validate_job(pending, self.group)["track_query"],
                "Next Artist Next Track",
            )
            self.assertFalse((pending / RETRY_STATE_FILENAME).exists())
            self.assertFalse((pending / "retry.txt").exists())
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        self.assertEqual(
            publish.call_args.args[0]["track_query"],
            "Next Artist Next Track",
        )
        self.assertNotIn("_skip_audio", publish.call_args.args[0])
        self.assertEqual(
            unavailable_track_keys(self.root),
            ["faithless sobersoul"],
        )
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )

    def test_durable_track_quarantine_replaces_track_after_retry_reset(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            directory = self.enqueue(
                self.job(dedupe_key="quarantine-without-retry-marker")
            )
        (self.root / "unavailable-tracks.json").write_text(
            json.dumps({"tracks": [{"key": "faithless sobersoul"}]}),
            encoding="utf-8",
        )
        publish = Mock()

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 0)
            publish.assert_not_called()
            self.assertEqual(
                validate_job(
                    self.root / "pending" / directory.name,
                    self.group,
                )["track_query"],
                "Next Artist Next Track",
            )
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        self.assertEqual(
            publish.call_args.args[0]["track_query"],
            "Next Artist Next Track",
        )
        self.assertTrue((self.root / "done" / directory.name).is_dir())
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )

    def test_replaced_job_ignores_stale_missing_track_marker_after_crash(self):
        entries = (
            ("faithless sobersoul", "Faithless Sobersoul"),
            ("next artist next track", "Next Artist Next Track"),
        )
        catalog = frozenset(key for key, _query in entries)
        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            directory = self.enqueue(
                self.job(dedupe_key="replacement-crash-window")
            )
        (self.root / "unavailable-tracks.json").write_text(
            json.dumps({"tracks": [{"key": "faithless sobersoul"}]}),
            encoding="utf-8",
        )
        _replace_job_track_query(
            directory,
            self.group,
            "Next Artist Next Track",
        )
        timestamp = "2026-08-01T12:00:00Z"
        (directory / RETRY_STATE_FILENAME).write_text(
            json.dumps(
                {
                    "schema": "vk_publish_retry.v2",
                    "attempts": 1,
                    "error_code": "vk_audio_no_match",
                    "unavailable_track_queries": ["faithless sobersoul"],
                    "first_failed_at": timestamp,
                    "last_failed_at": timestamp,
                }
            ),
            encoding="utf-8",
        )
        (directory / "retry.txt").write_text("fresh retry", encoding="utf-8")
        publish = Mock()

        with (
            patch(
                "vk_publish_queue._void_track_catalog_keys",
                return_value=catalog,
            ),
            patch(
                "vk_publish_queue._void_track_catalog_entries",
                return_value=entries,
            ),
        ):
            self.assertEqual(consume_once(self.root, self.group, publish), 0)

        publish.assert_called_once()
        self.assertEqual(
            publish.call_args.args[0]["track_query"],
            "Next Artist Next Track",
        )
        done = self.root / "done" / directory.name
        self.assertTrue(done.is_dir())
        self.assertFalse((done / RETRY_STATE_FILENAME).exists())
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["next artist next track"],
        )

    def test_retry_counter_does_not_reset_when_failure_class_changes(self):
        directory = self.enqueue(self.job(dedupe_key="retry-class-change"))
        first = Mock(
            side_effect=RetryablePublishError("audio search input is unavailable")
        )
        self.assertEqual(
            consume_once(self.root, self.group, first),
            RETRYABLE_EXIT_CODE,
        )

        pending = self.root / "pending" / directory.name
        os.utime(pending / "retry.txt", (0, 0))
        second = Mock(
            side_effect=RetryablePublishError("composer temporarily unavailable")
        )
        self.assertEqual(
            consume_once(self.root, self.group, second),
            RETRYABLE_EXIT_CODE,
        )

        retry_state = json.loads(
            (pending / RETRY_STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(retry_state["attempts"], 2)
        self.assertEqual(retry_state["error_code"], "vk_composer_unavailable")

    def test_consumer_recovers_interrupted_processing_job_without_receipt(self):
        job = self.job(dedupe_key="interrupted-before-publish")
        directory = self.enqueue(job)
        processing = self.root / "processing"
        processing.mkdir(exist_ok=True)
        os.replace(directory, processing / directory.name)
        publish = Mock()

        self.assertEqual(consume_once(self.root, self.group, publish), 0)

        publish.assert_called_once()
        self.assertTrue((self.root / "done" / directory.name).is_dir())
        self.assertFalse((processing / directory.name).exists())

    def test_consumer_reconciles_receipted_processing_job_without_republish(self):
        job = self.job(dedupe_key="interrupted-after-receipt")
        directory = self.enqueue(job)
        processing = self.root / "processing"
        processing.mkdir(exist_ok=True)
        os.replace(directory, processing / directory.name)
        self.write_receipt(job)
        publish = Mock()

        self.assertEqual(consume_once(self.root, self.group, publish), 0)

        publish.assert_not_called()
        self.assertTrue((self.root / "done" / directory.name).is_dir())
        self.assertFalse((processing / directory.name).exists())

    def test_retry_keeps_same_job_and_plan_then_writes_one_receipt(self):
        job = self.job(
            dedupe_key="composer-retry",
            plan_id="void-plan-retry-0001",
        )
        directory = self.enqueue(job)
        first = Mock(side_effect=RetryablePublishError("composer temporarily unavailable"))
        self.assertEqual(
            consume_once(self.root, self.group, first),
            RETRYABLE_EXIT_CODE,
        )

        pending = self.root / "pending" / directory.name
        retry_file = pending / "retry.txt"
        self.assertEqual(validate_job(pending, self.group)["job_id"], job["job_id"])
        self.assertEqual(validate_job(pending, self.group)["plan_id"], job["plan_id"])
        self.assertEqual(publication_receipts(self.root), [])

        os.utime(retry_file, (0, 0))
        success = Mock()
        self.assertEqual(consume_once(self.root, self.group, success), 0)
        success.assert_called_once()
        self.assertEqual(len(publication_receipts(self.root)), 1)
        self.assertEqual(consume_once(self.root, self.group, success), 0)
        success.assert_called_once()
        self.assertEqual(len(publication_receipts(self.root)), 1)

    def test_retry_exhaustion_quarantines_job_and_admin_requeue_resets_state(self):
        job = self.job(dedupe_key="retry-exhausted")
        directory = self.enqueue(job)
        publish = Mock(side_effect=RetryablePublishError("composer temporarily unavailable"))

        for attempt in range(1, MAX_RETRY_ATTEMPTS + 1):
            expected = 1 if attempt == MAX_RETRY_ATTEMPTS else RETRYABLE_EXIT_CODE
            self.assertEqual(consume_once(self.root, self.group, publish), expected)
            if attempt < MAX_RETRY_ATTEMPTS:
                pending = self.root / "pending" / directory.name
                os.utime(pending / "retry.txt", (0, 0))

        failed = self.root / "failed" / directory.name
        retry_state = json.loads(
            (failed / RETRY_STATE_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(retry_state["attempts"], MAX_RETRY_ATTEMPTS)
        self.assertIn("RetryExhaustedError", (failed / "error.txt").read_text())

        pending = requeue_failed(self.root, job["job_id"], self.group)
        self.assertFalse((pending / "retry.txt").exists())
        self.assertFalse((pending / RETRY_STATE_FILENAME).exists())

    def test_receipt_is_synced_before_publication_can_advance(self):
        self.enqueue(self.job(dedupe_key="durable-receipt"))

        with (
            patch("vk_publish_queue.os.fsync") as sync_file,
            patch("vk_publish_queue._sync_directory") as sync_directory,
        ):
            self.assertEqual(consume_once(self.root, self.group, Mock()), 0)

        sync_file.assert_called()
        self.assertEqual(
            sync_directory.call_args_list,
            [
                call(self.root / "published"),
                call(self.root),
            ],
        )

    def test_void_rotation_rolls_over_across_only_available_catalog_tracks(self):
        catalog = frozenset(
            {
                "rotation track a",
                "rotation track b",
                "rotation track unavailable",
            }
        )
        (self.root / "recent-tracks.json").write_text(
            json.dumps(
                {
                    "tracks": [
                        {"key": "rotation track a"},
                        {"key": "rotation track b"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        (self.root / "unavailable-tracks.json").write_text(
            json.dumps(
                {"tracks": [{"key": "rotation track unavailable"}]}
            ),
            encoding="utf-8",
        )

        with patch(
            "vk_publish_queue._void_track_catalog_keys",
            return_value=catalog,
        ):
            oldest = self.job(
                dedupe_key="available-rollover-oldest",
                track_query="Rotation Track A",
            )
            self.enqueue(oldest)

            still_recent = self.job(
                dedupe_key="available-rollover-recent",
                track_query="Rotation Track B",
            )
            with self.assertRaisesRegex(
                DuplicateTrackError,
                "other 1 available",
            ):
                self.enqueue(still_recent)

    def test_receipt_recovers_track_history_after_post_publish_write_failure(self):
        job = self.job(
            dedupe_key="receipt-recovers-track",
        )
        self.enqueue(job)

        with patch(
            "vk_publish_queue._record_published_track",
            side_effect=OSError("simulated history write failure"),
        ):
            self.assertEqual(consume_once(self.root, self.group, Mock()), 1)

        self.assertEqual(len(publication_receipts(self.root)), 1)
        with self.assertRaisesRegex(DuplicateJobError, "published job"):
            requeue_failed(self.root, job["job_id"], self.group)

        self.assertEqual(consume_once(self.root, self.group, Mock()), 0)
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["faithless sobersoul"],
        )

    def test_only_successful_publication_receipt_activates_draft_memory(self):
        with tempfile.TemporaryDirectory() as database_dir:
            database_path = os.path.join(database_dir, "void-test.db")
            with (
                patch("main.DB_PATH", database_path),
                patch("void_vk_producer.QUEUE_DIR", self.root),
            ):
                init_db()
                draft_id = save_draft(
                    "signal",
                    "test",
                    "publishable placeholder",
                    "VOID scheduled rubric",
                    "manual://vk/schedule/signal/test",
                    "HUMAN",
                )
                job = self.job(
                    dedupe_key=f"void-draft:{draft_id}",
                    source_ref=f"void:draft:{draft_id}",
                )
                self.enqueue(job)

                self.assertEqual(sync_published_drafts(), [])
                self.assertIsNone(get_draft(draft_id)["published_at"])

                self.assertEqual(consume_once(self.root, self.group, Mock()), 0)
                receipts = publication_receipts(self.root, producer="void")
                self.assertEqual(len(receipts), 1)
                self.assertNotIn("text", receipts[0])
                self.assertEqual(sync_published_drafts(), [draft_id])
                published = get_draft(draft_id)
                self.assertIsNotNone(published["published_at"])
                self.assertEqual(published["vk_job_id"], job["job_id"])
                self.assertEqual(published["vk_receipt_id"], job["job_id"])
                self.assertEqual(sync_published_drafts(), [])

    def test_consumer_backfills_receipts_for_existing_done_jobs(self):
        directory = self.enqueue(
            self.job(dedupe_key="legacy-done", source_ref="void:draft:42")
        )
        (self.root / "done").mkdir()
        os.replace(directory, self.root / "done" / directory.name)
        self.assertEqual(publication_receipts(self.root), [])

        publish = Mock()
        self.assertEqual(consume_once(self.root, self.group, publish), 0)

        publish.assert_not_called()
        receipts = publication_receipts(self.root, producer="void")
        self.assertEqual([item["source_ref"] for item in receipts], ["void:draft:42"])

    def test_unreadable_shared_track_history_fails_closed(self):
        (self.root / "recent-tracks.json").write_text("not-json", encoding="utf-8")

        with self.assertRaisesRegex(QueueValidationError, "history is unavailable"):
            self.enqueue(self.job(dedupe_key="history-corrupt"))

    def test_wrong_shape_duplicate_and_unnormalized_track_history_fail_closed(self):
        invalid_payloads = (
            [],
            {},
            {"tracks": {}},
            {"tracks": [None]},
            {"tracks": [{}]},
            {"tracks": [{"key": 1}]},
            {"tracks": [{"key": ""}]},
            {"tracks": [{"key": "artist track"}, {"key": "artist track"}]},
            {"tracks": [{"key": " Artist  Track "}]},
            {"tracks": [{"key": "artist track", "extra": "field"}]},
            {"tracks": [], "extra": True},
        )
        path = self.root / "recent-tracks.json"

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(QueueValidationError, "history is invalid"):
                    recent_track_keys(self.root, limit=None)

    def test_other_group_blocked(self):
        with self.assertRaises(QueueValidationError):
            validate_job(self.enqueue(self.job(target_group_id="1", dedupe_key="other-group")), self.group)

    def test_absolute_parent_and_symlink_media_blocked(self):
        for index, name in enumerate(("/tmp/x.png", "../x.png")):
            with self.subTest(name=name), self.assertRaises(QueueValidationError):
                enqueue_job(self.root, self.job(dedupe_key=f"bad-path-{index}", media=[name]), {name: b"x"})
        directory = self.enqueue(self.job(dedupe_key="linked"))
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path.name == "image-1.png" or original(path)):
            with self.assertRaises(QueueValidationError):
                validate_job(directory, self.group)

    def test_unknown_schema_and_action_blocked(self):
        for index, change in enumerate(({"schema": "other"}, {"action": "message"})):
            job = self.job(dedupe_key=f"bad-shape-{index}")
            job.update(change)
            with self.assertRaises(QueueValidationError):
                enqueue_job(self.root, job, {"image-1.png": b"x"})

    def test_producer_does_not_glob_private_consumer_states(self):
        original_glob = Path.glob

        def guarded_glob(path, pattern):
            if path.name in {"processing", "done", "failed"}:
                raise AssertionError(f"producer read private state: {path.name}")
            return original_glob(path, pattern)

        with patch.object(Path, "glob", guarded_glob):
            self.enqueue()

    def test_producer_only_rejects_deterministic_pending_conflict(self):
        self.enqueue()
        with self.assertRaises(QueueValidationError):
            self.enqueue()

    def test_producer_does_not_apply_global_dedupe_after_done(self):
        first = self.enqueue(); (self.root / "done").mkdir(); os.replace(first, self.root / "done" / first.name)
        second = self.enqueue()
        self.assertEqual(second.name, first.name)

    def test_consumer_global_dedupe_blocks_done_and_failed(self):
        for state in ("done", "failed"):
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    first = enqueue_job(root, self.job(), {"image-1.png": b"png"})
                    (root / state).mkdir()
                    os.replace(first, root / state / first.name)
                    enqueue_job(root, self.job(), {"image-1.png": b"png"})
                    publish = Mock()
                    self.assertEqual(consume_once(root, self.group, publish), 1)
                    publish.assert_not_called()
                    self.assertTrue((root / "failed" / first.name).is_dir())

    def test_consumer_global_dedupe_checks_pending_and_processing(self):
        for state in ("pending", "processing"):
            with self.subTest(state=state):
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    candidate = enqueue_job(root, self.job(), {"image-1.png": b"png"})
                    duplicate_dir = root / state / "zz-duplicate-marker"
                    duplicate_dir.mkdir(parents=True, exist_ok=True)
                    (duplicate_dir / "job.json").write_text('{"dedupe_key":"draft:296"}', encoding="utf-8")
                    publish = Mock()
                    result = consume_once(root, self.group, publish)
                    publish.assert_not_called()
                    self.assertEqual(result, 1)
                    self.assertTrue((root / "failed" / candidate.name).is_dir())

    def test_producer_allowlist(self):
        for producer in ("naz", "void"):
            self.assertEqual(validate_job(self.enqueue(self.job(dedupe_key=producer, producer=producer)), self.group)["producer"], producer)
        with self.assertRaises(QueueValidationError):
            self.job(dedupe_key="evil", producer="evil")

    def test_naz_contract_is_accepted_without_transformation(self):
        dedupe_key = "a" * 64
        job = build_job(producer="naz", target_group_id=self.group, text="Naz post", media=["image-1.png"], track_query="Artist Track", dedupe_key=dedupe_key, source_ref="schedule:2026-07-12:11:20", created_at="2026-07-12T08:20:00Z", not_before="2026-07-12T08:20:00Z")
        self.assertEqual(job["job_id"], canonical_job_id("naz", dedupe_key))
        accepted = validate_job(enqueue_job(self.root, job, {"image-1.png": b"png"}), self.group)
        self.assertEqual(accepted, job)

    def test_admin_requeue_preserves_dedupe_key(self):
        directory = self.enqueue()
        (self.root / "failed").mkdir()
        failed = self.root / "failed" / directory.name
        os.replace(directory, failed)
        (failed / "error.txt").write_text("failure", encoding="utf-8")
        target = requeue_failed(self.root, directory.name, self.group)
        self.assertEqual(validate_job(target, self.group)["dedupe_key"], "draft:296")
        self.assertFalse((target / "error.txt").exists())

    def test_consumer_has_no_forbidden_operations(self):
        source = Path(__file__).parents[1].joinpath("vk_queue_consumer.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("import main", source)
        for operation in ("messages.send", "likes.add", "wall.delete", "friends.add", "comments.create", "profile.edit"):
            self.assertNotIn(operation, source)

    def test_consumer_does_not_wait_for_vk_click_navigation(self):
        locator = Mock()
        locator.count.return_value = 1
        locator.first = locator
        locator.is_visible.return_value = True
        page = Mock()
        page.get_by_text.return_value = locator

        _click_first_text(page, ("Создать",))

        page.get_by_text.assert_called_once_with("Создать", exact=True)
        locator.click.assert_called_once_with(timeout=3_000, force=True, no_wait_after=True)

    def test_consumer_retries_when_vk_replaces_button_during_click(self):
        stale = Mock()
        stale.count.return_value = 1
        stale.first = stale
        stale.is_visible.return_value = True
        stale.click.side_effect = TimeoutError("button replaced")
        ready = Mock()
        ready.count.return_value = 1
        ready.first = ready
        ready.is_visible.return_value = True
        page = Mock()
        page.get_by_text.side_effect = [stale, ready]

        _click_first_text(page, ("Создать",), timeout=4_000)

        stale.click.assert_called_once_with(timeout=3_000, force=True, no_wait_after=True)
        ready.click.assert_called_once_with(timeout=3_000, force=True, no_wait_after=True)

    @patch("vk_queue_consumer._click_first_text")
    def test_consumer_scrolls_to_lazy_composer(self, click_text):
        editor = Mock()
        page = Mock()
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch("vk_queue_consumer._composer_input", side_effect=[None, None, editor]),
            patch("vk_queue_consumer._first_visible", return_value=None),
        ):
            _open_composer_once(page)

        page.mouse.wheel.assert_called_once_with(0, 900)
        page.wait_for_timeout.assert_any_call(1_500)
        self.assertGreaterEqual(click_text.call_count, 2)

    def test_composer_uses_data_role_aria_and_contenteditable_selectors(self):
        selectors = " ".join(COMPOSER_TRIGGER_SELECTORS + COMPOSER_INPUT_SELECTORS)
        self.assertIn("data-testid", selectors)
        self.assertIn("role=", selectors)
        self.assertIn("aria-label", selectors)
        self.assertIn("contenteditable", selectors)

    def test_transient_composer_absence_reloads_once(self):
        page = Mock()
        with patch(
            "vk_queue_consumer._open_composer_once",
            side_effect=[RetryablePublishError("temporary"), None],
        ) as attempt:
            _open_composer(page)
        self.assertEqual(attempt.call_count, 2)
        page.reload.assert_called_once_with(wait_until="domcontentloaded", timeout=15_000)

    def test_session_expiry_is_distinct_and_never_reloaded(self):
        page = Mock()
        with (
            patch(
                "vk_queue_consumer._open_composer_once",
                side_effect=VkAuthenticationRequiredError("authentication required"),
            ),
            self.assertRaises(VkAuthenticationRequiredError),
        ):
            _open_composer(page)
        page.reload.assert_not_called()

    def test_login_page_is_recognized_without_credentials(self):
        page = Mock(url="https://vk.com/login")
        self.assertTrue(_authentication_required(page))

    def test_post_author_marker_is_not_misclassified_as_authentication(self):
        hidden = Mock()
        hidden.count.return_value = 0
        page = Mock(url="https://vk.com/club237593988?post_author=1")
        page.locator.return_value = hidden
        self.assertFalse(_authentication_required(page))
        self.assertNotIn('[data-testid*="auth"]', " ".join(AUTH_SELECTORS))

    def test_auth_admin_notice_contains_metadata_only(self):
        with patch("vk_queue_consumer.QUEUE_DIR", self.root):
            path = _record_admin_notice("void-1234567890abcdef12345678", "vk_session_authentication_required")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["code"], "vk_session_authentication_required")
        self.assertEqual(set(payload), {"schema", "job_id", "code", "created_at"})

    def test_unknown_composer_structure_is_terminal(self):
        directory = self.enqueue(self.job(dedupe_key="terminal-structure"))
        publish = Mock(side_effect=VkComposerStructureError("unknown structure"))
        self.assertEqual(consume_once(self.root, self.group, publish), 1)
        self.assertTrue((self.root / "failed" / directory.name).is_dir())
        self.assertEqual(publication_receipts(self.root), [])


    def test_consumer_refuses_to_publish_when_vk_has_no_matching_track(self):
        rows = Mock()
        rows.count.return_value = 1
        row = Mock()
        row.inner_text.return_value = "Completely Different Audio"
        rows.nth.return_value = row
        search = Mock()
        search.count.return_value = 1
        search.nth.return_value = search
        search.is_visible.return_value = True
        page = Mock()
        page.locator.side_effect = [search, *([rows] * 6)]

        with (
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=0),
            patch("vk_queue_consumer._audio_title_fallback", return_value=(None, 0)),
            self.assertRaisesRegex(RetryablePublishError, "retry later"),
        ):
            _attach_track(page, "Requested Artist Requested Track")

        page.get_by_text.assert_not_called()

    def test_consumer_rejects_partial_audio_identity_matches(self):
        rows = Mock()
        rows.count.return_value = 1
        row = Mock()
        row.inner_text.return_value = "Requested Artist - Different Song"
        rows.nth.return_value = row
        search = Mock()
        search.count.return_value = 1
        search.nth.return_value = search
        search.is_visible.return_value = True
        page = Mock()
        page.locator.side_effect = [search, *([rows] * 6)]

        with (
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=0),
            patch("vk_queue_consumer._audio_title_fallback", return_value=(None, 0)),
            self.assertRaisesRegex(RetryablePublishError, "retry later"),
        ):
            _attach_track(page, "Requested Artist Requested Track")

        page.get_by_text.assert_not_called()

    def test_audio_identity_rejects_an_unrequested_version(self):
        query = "Requested Artist Requested Track"

        self.assertTrue(
            _audio_identity_matches(
                "Requested Artist - Requested Track 03:42",
                query,
            )
        )
        self.assertFalse(
            _audio_identity_matches(
                "Requested Artist - Requested Track (Live Remix)",
                query,
            )
        )
        self.assertTrue(
            _audio_identity_matches(
                "Requested Artist - Requested Track Remix",
                f"{query} Remix",
            )
        )

    def test_audio_identity_allows_distinctive_title_only_for_structured_vk_rows(self):
        query = "M83 — Midnight City"

        self.assertTrue(_audio_identity_matches("Midnight City", query))
        self.assertTrue(_audio_identity_matches("M83 Midnight City 04:03", query))
        self.assertFalse(_audio_identity_matches("Midnight City Live Remix", query))
        self.assertFalse(_audio_identity_matches("Heartbeat", "Nefretle — Heartbeat"))

        artist = Mock()
        artist.inner_text.return_value = "M83"
        artist.text_content.return_value = "M83"
        artist.get_attribute.return_value = None
        artists = Mock()
        artists.count.return_value = 1
        artists.nth.return_value = artist

        title = Mock()
        title.inner_text.return_value = "Midnight City"
        title.text_content.return_value = "Midnight City"
        title.get_attribute.return_value = None
        titles = Mock()
        titles.count.return_value = 1
        titles.nth.return_value = title

        row = Mock()
        row.inner_text.return_value = "Midnight City"
        row.text_content.return_value = "Midnight City"
        row.get_attribute.return_value = None
        row.locator.side_effect = lambda selector: (
            artists if "artist" in selector.casefold() else titles
        )

        self.assertIsNotNone(_audio_row_score(row, query))

    def test_attach_track_uses_accessible_text_and_title_only_fallback(self):
        row = Mock()
        row.inner_text.return_value = "Midnight City"
        row.text_content.return_value = "Midnight City"
        row.get_attribute.return_value = None
        artist = Mock()
        artist.inner_text.return_value = "M83"
        artist.text_content.return_value = "M83"
        artist.get_attribute.return_value = None
        artists = Mock()
        artists.count.return_value = 1
        artists.nth.return_value = artist
        title = Mock()
        title.inner_text.return_value = "Midnight City"
        title.text_content.return_value = "Midnight City"
        title.get_attribute.return_value = None
        titles = Mock()
        titles.count.return_value = 1
        titles.nth.return_value = title
        row.locator.side_effect = lambda selector: (
            artists if "artist" in selector.casefold() else titles
        )
        rows = Mock()
        rows.count.return_value = 1
        rows.nth.return_value = row
        search = Mock()
        search.count.return_value = 1
        search.nth.return_value = search
        search.is_visible.return_value = True
        done = Mock()
        done.last = done
        page = Mock()
        page.locator.side_effect = [search, *([rows] * 6)]
        page.get_by_text.return_value = done

        with (
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=0),
            patch("vk_queue_consumer._confirm_track_attached"),
        ):
            _attach_track(page, "M83 — Midnight City")

        row.click.assert_called_once_with(timeout=10_000)

    def test_audio_title_fallback_is_scoped_to_picker_and_exact_title(self):
        row = Mock()
        row.inner_text.return_value = "M83 Midnight City"
        row.text_content.return_value = "M83 Midnight City"
        row.get_attribute.return_value = None
        row.is_visible.return_value = True
        row_group = Mock()
        row_group.count.return_value = 1
        row_group.nth.return_value = row

        title = Mock()
        title.is_visible.return_value = True
        title.locator.return_value = row_group
        titles = Mock()
        titles.count.return_value = 1
        titles.nth.return_value = title
        scope = Mock()
        scope.is_visible.return_value = True
        scope.get_by_text.return_value = titles
        scopes = Mock()
        scopes.count.return_value = 1
        scopes.nth.return_value = scope
        search = Mock()
        search.locator.return_value = scopes
        page = Mock()

        selected, visible_count = _audio_title_fallback(
            page, search, "M83 — Midnight City"
        )

        self.assertIs(selected, row)
        self.assertEqual(visible_count, 1)
        scope.get_by_text.assert_called_once_with("Midnight City", exact=True)

    def test_audio_title_fallback_rejects_unscoped_and_one_word_titles(self):
        no_scopes = Mock()
        no_scopes.count.return_value = 0
        search = Mock()
        search.locator.return_value = no_scopes

        selected, count = _audio_title_fallback(
            Mock(), search, "M83 — Midnight City"
        )

        self.assertIsNone(selected)
        self.assertEqual(count, 0)

        one_word = Mock()
        one_word.is_visible.return_value = True
        no_clickable = Mock()
        no_clickable.count.return_value = 0
        one_word.locator.return_value = no_clickable
        titles = Mock()
        titles.count.return_value = 1
        titles.nth.return_value = one_word
        scope = Mock()
        scope.is_visible.return_value = True
        scope.get_by_text.return_value = titles
        scopes = Mock()
        scopes.count.return_value = 1
        scopes.nth.return_value = scope
        search.locator.return_value = scopes

        selected, count = _audio_title_fallback(
            Mock(), search, "Nefretle — Heartbeat"
        )

        self.assertIsNone(selected)
        self.assertEqual(count, 1)

    def test_audio_dom_diagnostics_keeps_structure_and_strips_content(self):
        search = Mock()
        search.evaluate.return_value = {
            "ancestors": [
                {
                    "tag": "DIV<script>",
                    "role": "dialog",
                    "testid": "audio-picker",
                    "className": "AudioRoot user secret@example.com",
                    "descendants": 42,
                    "text": "private post text",
                }
            ],
            "signatures": [
                {
                    "tag": "button",
                    "role": "option",
                    "testid": "audio-row",
                    "className": "AudioCard__root--abc",
                    "descendants": 3,
                }
            ],
        }

        diagnostic = _audio_dom_diagnostics(search)

        self.assertIn("audio-picker", diagnostic)
        self.assertIn("AudioCard__root--abc", diagnostic)
        self.assertNotIn("private post text", diagnostic)
        self.assertNotIn("<script>", diagnostic)

    def test_file_picker_search_is_never_accepted_as_audio(self):
        markers = Mock()
        markers.count.return_value = 2
        dialog = Mock()
        dialog.locator.return_value = markers
        dialogs = Mock()
        dialogs.count.return_value = 1
        dialogs.nth.return_value = dialog
        search = Mock()
        search.locator.return_value = dialogs

        self.assertTrue(_audio_search_is_file_picker(search))

        markers.count.return_value = 0
        self.assertFalse(_audio_search_is_file_picker(search))

    def test_current_vk_audio_cell_is_an_explicit_picker_trigger(self):
        self.assertEqual(
            AUDIO_PICKER_TRIGGER_SELECTORS[0],
            '[data-testid="posting_audio_select_audio_cell"]',
        )

    def test_current_vk_preview_item_is_checked_for_attached_audio(self):
        self.assertEqual(
            ATTACHED_AUDIO_SELECTORS[0],
            '[data-testid="posting_audio_select_audio_selected"]',
        )
        self.assertIn(
            '[data-testid="posting_audio_select_audio_selected_title"]',
            ATTACHED_AUDIO_SELECTORS,
        )

    def test_current_vk_wall_music_attachment_is_verified(self):
        self.assertEqual(
            PUBLISHED_AUDIO_SELECTORS[0],
            '[data-testid*="musicattach"]',
        )

    def test_wall_music_control_can_match_its_bounded_post_ancestor(self):
        parent = Mock()
        parent.inner_text.return_value = "M83 Midnight City"
        parent.text_content.return_value = "M83 Midnight City"
        parent.get_attribute.side_effect = lambda name: (
            "post" if name == "data-testid" else None
        )

        candidate = Mock()
        candidate.inner_text.return_value = ""
        candidate.text_content.return_value = ""
        candidate.get_attribute.return_value = None
        parents = Mock()
        parents.count.return_value = 1
        parents.inner_text = parent.inner_text
        parents.text_content = parent.text_content
        parents.get_attribute = parent.get_attribute
        candidate.locator.return_value = parents

        self.assertTrue(
            _locator_or_ancestor_audio_matches(
                candidate,
                "M83 — Midnight City",
            )
        )

    def test_unresolved_inspection_requires_existing_marker(self):
        with (
            patch("vk_queue_consumer._load_publication_attempt", return_value=None),
            self.assertRaisesRegex(RuntimeError, "no unresolved"),
        ):
            _inspect_unresolved_publication()

    def test_confirmed_unresolved_reconciliation_records_receipt_first(self):
        job = self.job(dedupe_key="confirmed-reconciliation")
        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch("vk_queue_consumer.publication_receipts", return_value=[]),
            patch("vk_queue_consumer._record_publication_receipt") as record,
            patch(
                "vk_queue_consumer._unresolved_publication_attempt",
                return_value=False,
            ) as resolve,
        ):
            _reconcile_confirmed_unresolved(job)

        self.assertEqual(
            record.call_args,
            call(self.root, job),
        )
        resolve.assert_called_once_with()

    def test_replacement_attempt_is_inspected_and_reconciled_with_audio(self):
        job = self.job(dedupe_key="replacement-inspect-reconcile")
        directory = self.enqueue(job)
        replacement = "NVTION PVNIC Back to Life"
        replaced_job = _replace_job_track_query(
            directory,
            self.group,
            replacement,
        )
        self.assertEqual(replaced_job["track_query"], replacement)
        (self.root / "unavailable-tracks.json").write_text(
            json.dumps({"tracks": [{"key": "faithless sobersoul"}]}),
            encoding="utf-8",
        )
        failed = self.root / "failed"
        failed.mkdir(exist_ok=True)
        os.replace(directory, failed / directory.name)
        profile = self.root / "profile"
        profile.mkdir()

        candidates = Mock()
        candidates.count.return_value = 0
        page = Mock()
        page.get_by_text.return_value = candidates
        page.evaluate.return_value = []
        context = Mock()
        context.new_page.return_value = page
        browser_api = Mock()
        browser_api.chromium.launch_persistent_context.return_value = context
        playwright_manager = MagicMock()
        playwright_manager.__enter__.return_value = browser_api
        playwright_manager.__exit__.return_value = False
        playwright_package = types.ModuleType("playwright")
        playwright_package.__path__ = []
        playwright_sync_api = types.ModuleType("playwright.sync_api")
        playwright_sync_api.sync_playwright = Mock(
            return_value=playwright_manager
        )
        playwright_package.sync_api = playwright_sync_api
        evidence = _PublicationEvidence(
            frozenset({"wall:-237593988_991"}),
            0,
            frozenset({"wall:-237593988_991"}),
        )

        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch("vk_queue_consumer.PROFILE_DIR", profile),
            patch("vk_queue_consumer.GROUP_ID", self.group),
            patch.dict(
                sys.modules,
                {
                    "playwright": playwright_package,
                    "playwright.sync_api": playwright_sync_api,
                },
            ),
            patch(
                "vk_queue_consumer.allowed_community_url",
                return_value="https://vk.com/club237593988",
            ),
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch(
                "vk_queue_consumer._published_post_evidence",
                return_value=evidence,
            ) as published_evidence,
        ):
            _record_publication_attempt(job["job_id"])
            self.assertEqual(
                _load_publication_attempt()["schema"],
                "vk_publication_attempt.v1",
            )
            self.assertEqual(
                _inspect_unresolved_publication(reconcile_confirmed=True),
                0,
            )

        self.assertEqual(published_evidence.call_args.args[2], replacement)
        self.assertNotIn("require_audio", published_evidence.call_args.kwargs)
        self.assertFalse((self.root / PUBLICATION_ATTEMPT_FILENAME).exists())
        receipts = publication_receipts(self.root)
        self.assertEqual(len(receipts), 1)
        self.assertEqual(recent_track_keys(self.root, limit=None), [])
        self.assertEqual(
            unavailable_track_keys(self.root),
            ["faithless sobersoul"],
        )

        publish = Mock()
        self.assertEqual(consume_once(self.root, self.group, publish), 0)
        publish.assert_not_called()
        self.assertTrue((self.root / "done" / job["job_id"]).is_dir())
        self.assertEqual(
            recent_track_keys(self.root, limit=None),
            ["nvtion pvnic back to life"],
        )

    def test_audio_trigger_diagnostics_contains_only_sanitized_testids(self):
        page = Mock()
        page.evaluate.return_value = [
            "posting_attach_audio",
            "<script>private text</script>",
            "posting_attach_audio",
        ]

        diagnostic = _audio_trigger_diagnostics(page)

        self.assertIn("posting_attach_audio", diagnostic)
        self.assertNotIn("<script>", diagnostic)
        self.assertEqual(diagnostic.count("posting_attach_audio"), 1)

    def test_audio_picker_text_lookup_is_scoped_to_active_composer(self):
        search = Mock()
        labels = Mock()
        labels.count.return_value = 1
        labels.last = labels
        labels.is_visible.return_value = True
        scope = Mock()
        scope.get_by_text.return_value = labels
        page = Mock()

        with (
            patch(
                "vk_queue_consumer._audio_search_input",
                side_effect=[None, search],
            ),
            patch(
                "vk_queue_consumer._first_visible",
                side_effect=[scope, None],
            ),
        ):
            self.assertIs(_open_audio_picker(page), search)

        scope.get_by_text.assert_called_once_with("Музыка", exact=True)
        page.get_by_text.assert_not_called()

    def test_audio_picker_prefers_exact_cell_before_text_fallback(self):
        search = Mock()
        trigger = Mock()
        scope = Mock()
        page = Mock()

        with (
            patch(
                "vk_queue_consumer._audio_search_input",
                side_effect=[None, search],
            ),
            patch(
                "vk_queue_consumer._first_visible",
                side_effect=[scope, trigger],
            ),
        ):
            self.assertIs(_open_audio_picker(page), search)

        trigger.click.assert_called_once_with(
            timeout=5_000,
            force=True,
            no_wait_after=True,
        )
        scope.get_by_text.assert_not_called()

    def test_consumer_prefers_the_closest_exact_audio_row(self):
        first = Mock()
        first.inner_text.return_value = "Requested Artist Requested Track"
        second = Mock()
        second.inner_text.return_value = (
            "Requested Artist Requested Track additional metadata"
        )
        rows = Mock()
        rows.count.return_value = 2
        rows.nth.side_effect = [first, second, first]
        search = Mock()
        search.count.return_value = 1
        search.nth.return_value = search
        search.is_visible.return_value = True
        page = Mock()
        page.locator.side_effect = [search, *([rows] * 6)]
        done = Mock()
        done.last = done
        page.get_by_text.return_value = done

        with (
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=0),
            patch("vk_queue_consumer._confirm_track_attached"),
        ):
            _attach_track(page, "Requested Artist Requested Track")

        first.click.assert_called_once_with(timeout=10_000)
        second.click.assert_not_called()

    def test_publication_cooldown_uses_latest_durable_receipt(self):
        older = self.job(dedupe_key="cooldown-older", source_ref="void:draft:1")
        newer = self.job(dedupe_key="cooldown-newer", source_ref="void:draft:2")
        self.write_receipt(older, "2026-08-01T11:00:00Z")
        self.write_receipt(newer, "2026-08-01T11:30:00Z")

        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch("vk_queue_consumer.PUBLISH_MIN_INTERVAL_SECONDS", 3600),
        ):
            self.assertEqual(
                _publication_cooldown_remaining(
                    datetime.fromisoformat("2026-08-01T12:00:00+00:00")
                ),
                1800,
            )
            self.assertEqual(
                _publication_cooldown_remaining(
                    datetime.fromisoformat("2026-08-01T12:31:00+00:00")
                ),
                0,
            )

    def test_active_publication_cooldown_does_not_open_consumer(self):
        with (
            patch("vk_queue_consumer.KILL_SWITCH", self.root / "disabled"),
            patch("vk_queue_consumer._unresolved_publication_attempt", return_value=False),
            patch("vk_queue_consumer._publication_cooldown_remaining", return_value=900),
            patch("vk_queue_consumer.consume_once") as consume,
        ):
            self.assertEqual(consume_queue(), 0)

        consume.assert_not_called()

    def test_attach_track_calls_composer_attachment_confirmation(self):
        row = Mock()
        row.inner_text.return_value = "Desired Artist - Midnight Signal"
        rows = Mock()
        rows.count.return_value = 1
        rows.nth.return_value = row
        search = Mock()
        search.count.return_value = 1
        search.nth.return_value = search
        search.is_visible.return_value = True
        done = Mock()
        page = Mock()
        page.locator.side_effect = [search, rows]
        page.get_by_text.return_value = done

        with (
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=3),
            patch("vk_queue_consumer._confirm_track_attached") as confirm,
        ):
            _attach_track(page, "Desired Artist Midnight Signal")

        confirm.assert_called_once_with(
            page,
            "Desired Artist Midnight Signal",
            3,
        )

    def test_track_attachment_requires_a_new_matching_composer_audio(self):
        page = Mock()
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch("vk_queue_consumer._first_visible", return_value=None),
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=2),
        ):
            _confirm_track_attached(
                page,
                "Requested Artist Requested Track",
                previous_match_count=1,
                timeout=0,
            )

    def test_track_attachment_does_not_accept_a_closed_picker_alone(self):
        page = Mock()
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch("vk_queue_consumer._first_visible", return_value=None),
            patch("vk_queue_consumer._visible_matching_audio_count", return_value=1),
            self.assertRaisesRegex(RetryablePublishError, "did not confirm"),
        ):
            _confirm_track_attached(
                page,
                "Requested Artist Requested Track",
                previous_match_count=1,
                timeout=0,
            )

    def test_saved_composer_attachments_are_removed_before_retry_upload(self):
        editor = Mock()
        editor.input_value.return_value = "post"
        remove = Mock()
        remove.is_visible.return_value = True
        item = Mock()
        item.is_visible.return_value = True
        items = Mock()
        items.count.side_effect = [1, 0, 0]
        items.nth.return_value = item
        controls = Mock()
        controls.count.return_value = 1
        controls.nth.return_value = remove
        scope = Mock()
        scope.locator.side_effect = lambda selector: (
            items if selector == '[data-testid="posting_attachment_item"]' else controls
        )
        page = Mock()

        with (
            patch("vk_queue_consumer._first_visible", return_value=scope),
            patch("vk_queue_consumer._post_input", return_value=editor),
        ):
            self.assertEqual(
                _clear_saved_composer_attachments(
                    page,
                    managed_texts=frozenset({"post"}),
                    job_id="void-1234567890abcdef12345678",
                ),
                1,
            )

        remove.click.assert_called_once_with(
            timeout=5_000,
            force=True,
            no_wait_after=True,
        )

    def test_hidden_remove_control_is_revealed_by_hover_before_cleanup(self):
        editor = Mock()
        editor.input_value.return_value = "post"
        remove = Mock()
        remove.is_visible.side_effect = [False, True]
        item = Mock()
        item.is_visible.return_value = True
        items = Mock()
        items.count.side_effect = [1, 0, 0]
        items.nth.return_value = item
        controls = Mock()
        controls.count.return_value = 1
        controls.nth.return_value = remove
        scope = Mock()
        scope.locator.side_effect = lambda selector: (
            items if selector == '[data-testid="posting_attachment_item"]' else controls
        )
        page = Mock()

        with (
            patch("vk_queue_consumer._first_visible", return_value=scope),
            patch("vk_queue_consumer._post_input", return_value=editor),
        ):
            self.assertEqual(
                _clear_saved_composer_attachments(
                    page,
                    managed_texts=frozenset({"post"}),
                    job_id="void-1234567890abcdef12345678",
                ),
                1,
            )

        item.hover.assert_called_once_with(timeout=3_000, force=True)
        remove.click.assert_called_once()

    def test_cleanup_fails_closed_when_attachment_item_does_not_disappear(self):
        editor = Mock()
        editor.input_value.return_value = "post"
        remove = Mock()
        remove.is_visible.return_value = True
        item = Mock()
        item.is_visible.return_value = True
        items = Mock()
        items.count.return_value = 1
        items.nth.return_value = item
        controls = Mock()
        controls.count.return_value = 1
        controls.nth.return_value = remove
        scope = Mock()
        scope.locator.side_effect = lambda selector: (
            items if selector == '[data-testid="posting_attachment_item"]' else controls
        )

        with (
            patch("vk_queue_consumer._first_visible", return_value=scope),
            patch("vk_queue_consumer._post_input", return_value=editor),
            self.assertRaisesRegex(RetryablePublishError, "did not disappear"),
        ):
            _clear_saved_composer_attachments(
                Mock(),
                managed_texts=frozenset({"post"}),
                job_id="void-1234567890abcdef12345678",
                removal_timeout=0,
            )

        remove.click.assert_called_once()

    def test_missing_composer_attachment_scope_is_retryable(self):
        with (
            patch("vk_queue_consumer._first_visible", return_value=None),
            self.assertRaisesRegex(RetryablePublishError, "attachment scope"),
        ):
            _clear_saved_composer_attachments(
                Mock(),
                managed_texts=frozenset({"post"}),
                job_id="void-1234567890abcdef12345678",
            )

    def test_image_upload_prefers_scoped_image_only_input_over_later_inputs(self):
        def file_input(accept, testid=None):
            candidate = Mock()
            candidate.get_attribute.side_effect = lambda name: {
                "accept": accept,
                "data-testid": testid,
            }.get(name)
            return candidate

        image_only = file_input("image/jpeg,image/png,image/gif")
        mixed_device = file_input(
            "video/mp4,image/jpeg,image/png",
            "posting_base_screen_download_from_device",
        )
        unrelated_empty = file_input("")
        inputs = Mock()
        inputs.count.return_value = 3
        inputs.nth.side_effect = [image_only, mixed_device, unrelated_empty]
        scope = Mock()
        scope.locator.return_value = inputs
        page = Mock()

        with patch("vk_queue_consumer._first_visible", return_value=scope):
            selected = _composer_image_file_input(page)
            selected.set_input_files(["image-1.png"])

        self.assertIs(selected, image_only)
        image_only.set_input_files.assert_called_once_with(["image-1.png"])
        mixed_device.set_input_files.assert_not_called()
        unrelated_empty.set_input_files.assert_not_called()
        page.locator.assert_not_called()

    def test_image_upload_uses_exact_portal_input_when_scope_has_no_image_input(self):
        empty = Mock()
        empty.get_attribute.side_effect = lambda name: "" if name == "accept" else None
        scoped_inputs = Mock()
        scoped_inputs.count.return_value = 1
        scoped_inputs.nth.return_value = empty
        scope = Mock()
        scope.locator.return_value = scoped_inputs

        mixed_device = Mock()
        mixed_device.get_attribute.side_effect = lambda name: {
            "accept": "video/mp4,image/jpeg,image/png",
            "data-testid": "posting_base_screen_download_from_device",
        }.get(name)
        portal_inputs = Mock()
        portal_inputs.count.return_value = 1
        portal_inputs.nth.return_value = mixed_device
        page = Mock()
        page.locator.return_value = portal_inputs

        with patch("vk_queue_consumer._first_visible", return_value=scope):
            selected = _composer_image_file_input(page)

        self.assertIs(selected, mixed_device)
        page.locator.assert_called_once_with(
            'input[type="file"]'
            '[data-testid="posting_base_screen_download_from_device"]'
        )

    def test_image_upload_rejects_empty_audio_and_non_image_inputs(self):
        empty = Mock()
        empty.get_attribute.side_effect = lambda name: "" if name == "accept" else None
        audio = Mock()
        audio.get_attribute.side_effect = lambda name: (
            "audio/mpeg,image/jpeg" if name == "accept" else None
        )
        scoped_inputs = Mock()
        scoped_inputs.count.return_value = 2
        scoped_inputs.nth.side_effect = [empty, audio]
        scope = Mock()
        scope.locator.return_value = scoped_inputs

        video = Mock()
        video.get_attribute.side_effect = lambda name: (
            "video/mp4" if name == "accept" else None
        )
        portal_inputs = Mock()
        portal_inputs.count.return_value = 1
        portal_inputs.nth.return_value = video
        page = Mock()
        page.locator.return_value = portal_inputs

        with (
            patch("vk_queue_consumer._first_visible", return_value=scope),
            self.assertRaisesRegex(RetryablePublishError, "image upload input"),
        ):
            _composer_image_file_input(page)

    def test_unmanaged_saved_composer_draft_is_never_overwritten(self):
        editor = Mock()
        editor.input_value.return_value = "manual administrator draft"
        item = Mock()
        item.is_visible.return_value = True
        items = Mock()
        items.count.return_value = 1
        items.nth.return_value = item
        scope = Mock()
        scope.locator.return_value = items
        with (
            patch("vk_queue_consumer._first_visible", return_value=scope),
            patch("vk_queue_consumer._post_input", return_value=editor),
            patch("vk_queue_consumer._record_admin_notice") as notice,
            self.assertRaisesRegex(VkComposerStructureError, "unmanaged"),
        ):
            _clear_saved_composer_attachments(
                Mock(),
                managed_texts=frozenset({"current queue post"}),
                job_id="void-1234567890abcdef12345678",
            )
        notice.assert_called_once_with(
            "void-1234567890abcdef12345678",
            "vk_unmanaged_saved_composer_draft",
        )
        item.hover.assert_not_called()

    def test_published_post_evidence_requires_text_and_matching_audio(self):
        matching_audio = Mock()
        matching_audio.is_visible.return_value = True
        matching_audio.inner_text.return_value = "Desired Artist - Midnight Signal"
        matching_audio.text_content.return_value = "Desired Artist - Midnight Signal"
        matching_audio.get_attribute.return_value = None
        audio_rows = Mock()
        audio_rows.count.return_value = 1
        audio_rows.nth.return_value = matching_audio

        post = Mock()
        post.is_visible.return_value = True
        post.inner_text.return_value = "Exact post body\nDesired Artist - Midnight Signal"
        post.text_content.return_value = "Exact post body Desired Artist - Midnight Signal"
        post.get_attribute.side_effect = lambda name: (
            "-237593988_777" if name == "data-post-id" else None
        )
        post.locator.return_value = audio_rows
        posts = Mock()
        posts.count.return_value = 1
        posts.nth.return_value = post
        page = Mock()
        page.locator.return_value = posts

        evidence = _published_post_evidence(
            page,
            "Exact post body",
            "Desired Artist Midnight Signal",
        )

        self.assertEqual(evidence.identified, frozenset({"wall:-237593988_777"}))
        self.assertEqual(evidence.anonymous_count, 0)

        matching_audio.inner_text.return_value = "Desired Artist - Different Song"
        matching_audio.text_content.return_value = "Desired Artist - Different Song"
        wrong_audio_evidence = _published_post_evidence(
            page,
            "Exact post body",
            "Desired Artist Midnight Signal",
        )
        self.assertEqual(wrong_audio_evidence.identified, frozenset())
        self.assertEqual(
            wrong_audio_evidence.observed_identified,
            frozenset({"wall:-237593988_777"}),
        )

    def test_publish_confirmation_rejects_only_preexisting_matching_post(self):
        before = _PublicationEvidence(frozenset({"wall:-237593988_1"}), 0)
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch("vk_queue_consumer._published_post_evidence", return_value=before),
            self.assertRaisesRegex(VkPublishConfirmationError, "no new post"),
        ):
            _wait_for_publication_confirmation(
                Mock(),
                "Exact post body",
                "Requested Artist Requested Track",
                before,
                timeout=0,
            )

    def test_publish_confirmation_accepts_a_new_matching_post_id(self):
        before = _PublicationEvidence(frozenset({"wall:-237593988_1"}), 0)
        after = _PublicationEvidence(
            frozenset({"wall:-237593988_1", "wall:-237593988_2"}),
            0,
        )
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch("vk_queue_consumer._published_post_evidence", return_value=after),
        ):
            _wait_for_publication_confirmation(
                Mock(),
                "Exact post body",
                "Requested Artist Requested Track",
                before,
                timeout=0,
            )

    def test_publish_confirmation_rejects_lazy_audio_on_an_old_post(self):
        before = _PublicationEvidence(
            frozenset(),
            0,
            frozenset({"wall:-237593988_1"}),
        )
        old_post_after_audio_load = _PublicationEvidence(
            frozenset({"wall:-237593988_1"}),
            0,
            frozenset({"wall:-237593988_1"}),
        )
        with (
            patch("vk_queue_consumer._authentication_required", return_value=False),
            patch(
                "vk_queue_consumer._published_post_evidence",
                return_value=old_post_after_audio_load,
            ),
            self.assertRaises(VkPublishConfirmationError),
        ):
            _wait_for_publication_confirmation(
                Mock(),
                "Exact post body",
                "Requested Artist Requested Track",
                before,
                timeout=0,
            )

    def test_publish_attempt_marker_survives_browser_proof_until_receipt(self):
        job = self.job(dedupe_key="browser-proof-marker")
        marker = self.root / PUBLICATION_ATTEMPT_FILENAME
        button = Mock()
        button.click.side_effect = lambda **_kwargs: self.assertTrue(marker.is_file())
        controls = Mock()
        controls.count.return_value = 1
        controls.last = button
        button.is_visible.return_value = True
        page = Mock()
        page.get_by_text.return_value = controls

        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch("vk_queue_consumer._wait_for_publication_confirmation") as confirm,
        ):
            _publish_and_confirm(
                page,
                job,
                _PublicationEvidence(frozenset(), 0),
            )
            self.assertTrue(marker.is_file())
            self.assertTrue(_unresolved_publication_attempt())

            self.write_receipt(job)
            self.assertFalse(_unresolved_publication_attempt())
            self.assertFalse(marker.exists())

        button.click.assert_called_once_with(
            timeout=15_000,
            force=True,
            no_wait_after=True,
        )
        confirm.assert_called_once()

    def test_ambiguous_publish_blocks_following_consumer_runs(self):
        job = self.job(dedupe_key="ambiguous-browser-publish")
        button = Mock()
        controls = Mock()
        controls.count.return_value = 1
        controls.last = button
        button.is_visible.return_value = True
        page = Mock()
        page.get_by_text.return_value = controls

        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch(
                "vk_queue_consumer._wait_for_publication_confirmation",
                side_effect=VkPublishConfirmationError("no new post"),
            ),
            self.assertRaises(VkPublishConfirmationError),
        ):
            _publish_and_confirm(
                page,
                job,
                _PublicationEvidence(frozenset(), 0),
            )

        marker = self.root / PUBLICATION_ATTEMPT_FILENAME
        self.assertTrue(marker.is_file())
        self.assertEqual(publication_receipts(self.root), [])
        with (
            patch("vk_queue_consumer.QUEUE_DIR", self.root),
            patch("vk_queue_consumer.KILL_SWITCH", self.root / "enabled"),
            patch("vk_queue_consumer.consume_once") as consume,
        ):
            self.assertEqual(consume_queue(), 75)
        consume.assert_not_called()

    def test_missing_vk_audio_search_is_retryable(self):
        missing = Mock()
        missing.count.return_value = 0
        page = Mock()
        page.locator.return_value = missing
        page.get_by_text.return_value = missing

        with (
            patch("vk_queue_consumer.time.monotonic", side_effect=range(100)),
            self.assertRaisesRegex(RetryablePublishError, "audio search input"),
        ):
            _attach_track(page, "Requested Artist Requested Track")


if __name__ == "__main__":
    unittest.main()
