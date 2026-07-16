import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import (
    VOID_TO_NAZ_FORBIDDEN_OPENINGS,
    VOID_TO_NAZ_OPENING_OPTIONS,
    build_void_to_naz_exchange_payload,
    build_rubric_header,
    clean_source_lines,
    choose_vk_music_track,
    current_void_schedule_slot,
    display_source_name,
    eligible_schedule_slots,
    eligible_rubric_slots,
    inject_rubric_header,
    parse_daily_times,
    post_vk_vibes,
    track_vk_vibes,
    too_much_english,
    trim_post,
    validate_void_fragment_for_naz,
)
from void_core import CONTENT_PLAN, RUBRIC_SCHEDULE, TELEGRAM_VOID_SCHEDULE


class AutopostingRubricTests(unittest.TestCase):
    def test_void_daily_times_are_normalized_and_deduplicated(self) -> None:
        self.assertEqual(
            parse_daily_times("12:00, 16:00,20:00,00:00,12:00,bad,24:00"),
            ("12:00", "16:00", "20:00", "00:00"),
        )

    def test_void_schedule_recognizes_all_requested_moscow_slots(self) -> None:
        schedule = ("12:00", "16:00", "20:00", "00:00")
        for hour in (12, 16, 20):
            with self.subTest(hour=hour):
                self.assertEqual(
                    current_void_schedule_slot(datetime(2026, 7, 15, hour, 0), schedule),
                    f"2026-07-15:{hour:02d}:00",
                )
        self.assertEqual(
            current_void_schedule_slot(datetime(2026, 7, 16, 0, 0), schedule),
            "2026-07-16:00:00",
        )
        self.assertIsNone(current_void_schedule_slot(datetime(2026, 7, 15, 13, 0), schedule))

    def test_build_rubric_header_for_news_mode(self) -> None:
        self.assertEqual(build_rubric_header("news", "AI"), "SIGNAL / AI")

    def test_build_rubric_header_for_void_modes(self) -> None:
        self.assertEqual(build_rubric_header("frequency", "HUMAN"), "FREQUENCY")
        self.assertEqual(build_rubric_header("archive", "DIGEST"), "SIGNAL ARCHIVE")
        self.assertEqual(build_rubric_header("vault", "HUMAN"), "THE VAULT")

    def test_content_plan_has_non_news_modes(self) -> None:
        modes = {slot["mode"] for slot in CONTENT_PLAN}
        self.assertIn("frequency", modes)
        self.assertIn("observation", modes)
        self.assertIn("vault", modes)

    def test_rubric_schedule_is_void_owned(self) -> None:
        voices = {slot["voice"] for slot in RUBRIC_SCHEDULE}
        self.assertIn("void", voices)
        self.assertIn("news", voices)
        self.assertNotIn("naz", voices)
        self.assertNotIn("dialog", voices)

    def test_midnight_is_only_eligible_at_night(self) -> None:
        night = datetime(2026, 7, 9, 1, 0)
        day = datetime(2026, 7, 9, 14, 0)
        night_modes = {slot["mode"] for slot in eligible_rubric_slots(night)}
        day_modes = {slot["mode"] for slot in eligible_rubric_slots(day)}
        self.assertIn("midnight", night_modes)
        self.assertNotIn("midnight", day_modes)

    def test_telegram_schedule_is_void_owned(self) -> None:
        self.assertTrue(all(slot["voice"] in {"void", "news"} for slot in TELEGRAM_VOID_SCHEDULE))

    def test_telegram_void_midnight_is_night_only(self) -> None:
        night = datetime(2026, 7, 9, 1, 0)
        day = datetime(2026, 7, 9, 14, 0)
        night_modes = {slot["mode"] for slot in eligible_schedule_slots(TELEGRAM_VOID_SCHEDULE, night)}
        day_modes = {slot["mode"] for slot in eligible_schedule_slots(TELEGRAM_VOID_SCHEDULE, day)}
        self.assertIn("midnight", night_modes)
        self.assertNotIn("midnight", day_modes)

    def test_inject_rubric_header_into_post(self) -> None:
        post = inject_rubric_header("news", "AI", "Текст поста")
        self.assertTrue(post.startswith("SIGNAL / AI"))


    def test_quality_allows_english_brand_terms_in_russian_post(self) -> None:
        post = (
            "SIGNAL / AI\n\n"
            "OpenAI, Tesla, Claude, MIT Technology Review и The Verge "
            "могут быть частью русского поста, если основной текст остается "
            "на русском языке и не превращается в английскую заметку."
        )
        self.assertFalse(too_much_english(post))

    def test_trim_post_preserves_source_block(self) -> None:
        body = ("Абзац с завершенной мыслью. " * 80).strip()
        post = f"{body}\n\nИсточник: VOID\nhttps://example.com/source"
        trimmed = trim_post(post, limit=500)
        self.assertLessEqual(len(trimmed), 500)
        self.assertIn("Источник: VOID", trimmed)
        self.assertIn("https://example.com/source", trimmed)


    def test_clean_source_lines_removes_question_mark_source(self) -> None:
        post = "SIGNAL\n\nText\n\n???????: VOID internal signal"
        self.assertEqual(clean_source_lines(post), "SIGNAL\n\nText")
        self.assertEqual(display_source_name("VOID internal signal"), "VOID")

    def test_void_fragment_for_naz_blocks_secrets(self) -> None:
        ok, reason = validate_void_fragment_for_naz("Вот BOT_TOKEN=123 и ssh root@147.45.154.248")
        self.assertFalse(ok)
        self.assertTrue(reason)

    def test_void_fragment_for_naz_allows_public_signal(self) -> None:
        ok, reason = validate_void_fragment_for_naz(
            "AI не ломается в момент ошибки. Он ломается раньше: когда человек решает, "
            "что проверка результата уже не нужна."
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_void_to_naz_payload_requires_naz_adaptation(self) -> None:
        fragment = "Инструмент становится клеткой, когда человек перестаёт замечать его форму."
        payload = build_void_to_naz_exchange_payload(
            fragment,
            source_event="test",
            topic="Инструменты",
        )

        self.assertEqual(payload["exchange_kind"], "private_thought")
        self.assertEqual(payload["schema"], "private_thought.v1")
        self.assertEqual(payload["text"], fragment)
        self.assertFalse(payload["ready_to_publish"])
        self.assertFalse(payload["already_published"])
        self.assertTrue(payload["requires_adaptation"])
        self.assertTrue(payload["public_attribution_allowed"])
        self.assertEqual(payload["adaptation_role"], "naz_original_reflection_after_private_conversation")

    def test_void_to_naz_openings_have_backstage_variety(self) -> None:
        self.assertIn("Из тёмного угла прилетело:", VOID_TO_NAZ_OPENING_OPTIONS)
        self.assertGreaterEqual(len(VOID_TO_NAZ_OPENING_OPTIONS), 6)
        self.assertIn(
            "Void опять говорит странно, но по делу:",
            VOID_TO_NAZ_FORBIDDEN_OPENINGS,
        )

    def test_vk_music_rotation_skips_recent_track(self) -> None:
        tracks = [
            {"artist": "A", "title": "First", "tags": ["future"]},
            {"artist": "B", "title": "Second", "tags": ["future"]},
            {"artist": "C", "title": "Third", "tags": ["future"]},
        ]
        draft = {
            "id": 42,
            "mode": "future",
            "title": "Future signal",
            "frequency": "AI",
            "post": "future systems",
        }

        with (
            patch("main.load_vk_music_tracks", return_value=tracks),
            patch("main.recent_vk_music_track_keys", return_value=["a|first", "b|second"]),
        ):
            selected = choose_vk_music_track(draft)

        self.assertEqual(selected["title"], "Third")

    def test_vk_music_vibes_come_from_post_mode_and_track_title(self) -> None:
        draft = {
            "mode": "midnight",
            "title": "Ночной сигнал",
            "frequency": "HUMAN",
            "post": "Тихая память города после дождя.",
        }
        track = {"artist": "Example", "title": "Silent Night Rain", "tags": ["music"]}

        self.assertTrue({"night", "calm", "melancholy"} <= post_vk_vibes(draft))
        self.assertTrue({"night", "calm", "melancholy"} <= track_vk_vibes(track))


if __name__ == "__main__":
    unittest.main()
