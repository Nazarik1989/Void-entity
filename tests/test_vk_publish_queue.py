import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import get_draft, init_db, save_draft
from vk_browser_publisher import parse_scheduled_draft_id
from vk_publish_queue import (
    DuplicateTrackError,
    QueueValidationError,
    RetryablePublishError,
    build_job,
    canonical_job_id,
    consume_once,
    enqueue_job,
    publication_receipts,
    recent_track_keys,
    requeue_failed,
    validate_job,
)
from vk_queue_consumer import (
    AUTH_SELECTORS,
    COMPOSER_INPUT_SELECTORS,
    COMPOSER_TRIGGER_SELECTORS,
    VkAuthenticationRequiredError,
    VkComposerStructureError,
    _attach_track,
    _authentication_required,
    _click_first_text,
    _open_composer,
    _open_composer_once,
    _record_admin_notice,
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
        values = dict(producer="void", target_group_id=self.group, text="post", media=["image-1.png"], track_query="track", dedupe_key="draft:296", source_ref="void:draft:296")
        values.update(changes)
        return build_job(**values)

    def enqueue(self, job=None):
        return enqueue_job(self.root, job or self.job(), {"image-1.png": b"png"})

    def test_parse_scheduled_draft_id_regression(self):
        self.assertEqual(parse_scheduled_draft_id("Scheduled VK draft: #296"), 296)

    def test_valid_job(self):
        self.assertEqual(validate_job(self.enqueue(), self.group)["producer"], "void")

    def test_every_job_requires_a_music_track(self):
        with self.assertRaisesRegex(QueueValidationError, "track_query is required"):
            self.job(track_query="")

    def test_shared_recent_eight_tracks_cover_naz_and_void(self):
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
            producer="void",
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

    def test_missing_vk_track_stays_pending_for_safe_retry(self):
        directory = self.enqueue(
            self.job(dedupe_key="retry-track", track_query="Rare Track")
        )
        publish = Mock(
            side_effect=RetryablePublishError("no matching VK audio result")
        )

        self.assertEqual(consume_once(self.root, self.group, publish), 0)

        pending = self.root / "pending" / directory.name
        self.assertTrue(pending.is_dir())
        self.assertTrue((pending / "retry.txt").is_file())
        self.assertFalse((self.root / "done" / directory.name).exists())
        self.assertFalse((self.root / "failed" / directory.name).exists())
        self.assertEqual(publication_receipts(self.root), [])

        publish.reset_mock()
        self.assertEqual(consume_once(self.root, self.group, publish), 0)
        publish.assert_not_called()

    def test_retry_keeps_same_job_and_plan_then_writes_one_receipt(self):
        job = self.job(
            dedupe_key="composer-retry",
            plan_id="void-plan-retry-0001",
        )
        directory = self.enqueue(job)
        first = Mock(side_effect=RetryablePublishError("composer temporarily unavailable"))
        self.assertEqual(consume_once(self.root, self.group, first), 0)

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
        page.locator.side_effect = [search, rows]

        with self.assertRaisesRegex(RetryablePublishError, "retry later"):
            _attach_track(page, "Requested Artist Requested Track")

        page.get_by_text.assert_not_called()

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
