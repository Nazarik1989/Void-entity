import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from vk_browser_publisher import parse_scheduled_draft_id
from vk_publish_queue import QueueValidationError, build_job, canonical_job_id, consume_once, enqueue_job, requeue_failed, validate_job


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

    def test_duplicate_dedupe_key_blocked(self):
        first = self.enqueue(); (self.root / "done").mkdir(); os.replace(first, self.root / "done" / first.name)
        with self.assertRaises(QueueValidationError):
            self.enqueue()

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


if __name__ == "__main__":
    unittest.main()
