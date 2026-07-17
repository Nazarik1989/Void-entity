import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import (
    VOID_TO_NAZ_FORBIDDEN_OPENINGS,
    VOID_TO_NAZ_OPENING_OPTIONS,
    build_void_to_naz_exchange_payload,
    build_rubric_header,
    build_prompt,
    clean_source_lines,
    choose_vk_music_track,
    current_void_schedule_slot,
    display_source_name,
    eligible_schedule_slots,
    eligible_rubric_slots,
    generate_scheduled_draft,
    get_recent_content_signatures,
    init_db,
    inject_rubric_header,
    parse_daily_times,
    post_vk_vibes,
    quality_check,
    record_content_signature,
    repeats_default_digital_thesis,
    semantic_repetition_reason,
    semantic_theme_candidates,
    track_vk_vibes,
    too_much_english,
    trim_post,
    validate_void_fragment_for_naz,
)
from void_core import (
    CONTENT_PLAN,
    MODE_SEMANTIC_THEMES,
    RUBRIC_SCHEDULE,
    TELEGRAM_VOID_SCHEDULE,
    VOID_CORE_PROMPT,
)


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

    def test_vk_prompt_uses_vk_context_instead_of_telegram_context(self) -> None:
        prompt = build_prompt("observation", "ATTENTION", platform="vk")
        self.assertIn("PLATFORM: vk", prompt)
        self.assertIn("VK public page", prompt)
        self.assertIn("visual-first post", prompt)
        self.assertNotIn("editor of the VOID Telegram channel", prompt)

    def test_character_core_is_a_lens_not_a_mandatory_thesis(self) -> None:
        self.assertIn("not a mandatory topic or conclusion", VOID_CORE_PROMPT)
        self.assertIn("Do not force every post back to digital noise", VOID_CORE_PROMPT)
        self.assertNotIn("The center is the human inside the digital world", VOID_CORE_PROMPT)
        self.assertIn("one concrete subject", VOID_CORE_PROMPT)

    def test_semantic_theme_cooldown_happens_before_generation(self) -> None:
        recent = [
            {"semantic_theme": "body"},
            {"semantic_theme": "craft"},
            {"semantic_theme": "city"},
            {"semantic_theme": "work"},
            {"semantic_theme": "relationship"},
            {"semantic_theme": "play"},
        ]
        candidates = semantic_theme_candidates("signal", recent)
        self.assertTrue(candidates)
        current_cooldown = {item["semantic_theme"] for item in recent[-5:]}
        self.assertTrue(set(candidates).isdisjoint(current_cooldown))
        self.assertIn("body", candidates)
        self.assertGreaterEqual(len(MODE_SEMANTIC_THEMES["signal"]), 6)

    def test_semantic_gate_catches_the_recurring_thesis_not_one_word(self) -> None:
        candidate = (
            "Платформа снова превращает привычку в систему. Внимание рассеивается, "
            "и человек пытается сохранить свободу выбора и не потерять себя."
        )
        recent = [
            "Цифровая система забирает фокус, пока человек старается сохранить себя.",
            "Экран рассеивает внимание, но человеческий выбор всё ещё остаётся живым.",
        ]
        self.assertTrue(repeats_default_digital_thesis(candidate))
        self.assertEqual(
            semantic_repetition_reason(candidate, recent),
            "repeated_digital_attention_thesis",
        )

    def test_generation_diagnostics_are_never_publishable(self) -> None:
        ok, reason = quality_check("x" * 300 + "\nDIAG: RuntimeError")
        self.assertFalse(ok)
        self.assertIn("diagnostic", reason)

    def test_semantic_theme_is_persisted_in_signature_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            database_path = os.path.join(temp_dir, "void-test.db")
            with patch("main.DB_PATH", database_path):
                init_db()
                record_content_signature(
                    {
                        "platform": "vk",
                        "facet": "observer",
                        "intent": "notice",
                        "format": "scene",
                        "content_format": "text_story",
                        "content_kind": "text",
                        "semantic_theme": "maintenance",
                        "hook": "detail",
                        "media": "object",
                    },
                    "test topic",
                )
                recent = get_recent_content_signatures()
        self.assertEqual(recent[-1]["semantic_theme"], "maintenance")

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

    def test_vk_music_rotation_respects_shared_naz_and_void_history(self) -> None:
        tracks = [
            {
                "artist": f"Artist {index}",
                "title": f"Future {index}",
                "tags": ["future"],
            }
            for index in range(9)
        ]
        draft = {
            "id": 99,
            "mode": "future",
            "title": "Future signal",
            "frequency": "AI",
            "post": "future systems",
        }
        excluded = {f"artist {index} future {index}" for index in range(8)}

        with (
            patch("main.load_vk_music_tracks", return_value=tracks),
            patch("main.recent_vk_music_track_keys", return_value=[]),
        ):
            selected = choose_vk_music_track(draft, excluded_track_keys=excluded)

        self.assertEqual(selected["title"], "Future 8")

    def test_vk_music_selection_blocks_without_fresh_suitable_track(self) -> None:
        tracks = [{"artist": "Artist", "title": "Future", "tags": ["future"]}]
        draft = {
            "id": 100,
            "mode": "future",
            "title": "Future signal",
            "frequency": "AI",
            "post": "future systems",
        }

        with (
            patch("main.load_vk_music_tracks", return_value=tracks),
            patch("main.recent_vk_music_track_keys", return_value=[]),
        ):
            selected = choose_vk_music_track(
                draft,
                excluded_track_keys={"artist future"},
            )

        self.assertIsNone(selected)

    def test_vk_producer_timer_uses_only_requested_moscow_slots(self) -> None:
        timer = Path("deploy/systemd/void-vk-producer.timer").read_text(
            encoding="utf-8"
        )

        self.assertIn("OnCalendar=*-*-* 13:30:00 Europe/Moscow", timer)
        self.assertIn("OnCalendar=*-*-* 20:30:00 Europe/Moscow", timer)
        self.assertIn(
            "OnCalendar=Fri,Sat *-*-* 23:30:00 Europe/Moscow", timer
        )
        self.assertNotIn("00,03,06,09,12,15,18,21", timer)


class ScheduledSemanticDiversityTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _draft(post: str) -> dict:
        return {
            "mode": "signal",
            "title": "Тест",
            "post": post,
            "source_name": "VOID",
            "source_url": "manual://vk/schedule/signal/test",
            "frequency": "HUMAN",
            "publish_score": 8,
        }

    async def test_bounded_retry_stops_after_two_rejected_candidates(self) -> None:
        with (
            patch("main.recent_scheduled_posts", return_value=["old"]),
            patch("main.generate_post_sync", return_value=self._draft("candidate")) as generate,
            patch("main.quality_check", return_value=(True, "ok")),
            patch("main.semantic_repetition_reason", return_value="near_duplicate_semantics"),
            patch("main.save_draft") as save,
        ):
            with self.assertRaises(RuntimeError):
                await generate_scheduled_draft(
                    mode="signal",
                    content="content",
                    frequency="HUMAN",
                    source_name="VOID",
                    source_url="manual://vk/schedule/signal/test",
                    platform="vk",
                    semantic_theme="craft",
                )
        self.assertEqual(generate.call_count, 2)
        save.assert_not_called()

    async def test_one_retry_can_succeed_and_only_accepted_draft_is_saved(self) -> None:
        with (
            patch("main.recent_scheduled_posts", return_value=["old"]),
            patch("main.generate_post_sync", return_value=self._draft("candidate")) as generate,
            patch("main.quality_check", return_value=(True, "ok")),
            patch(
                "main.semantic_repetition_reason",
                side_effect=["near_duplicate_semantics", ""],
            ),
            patch("main.save_draft", return_value=77) as save,
        ):
            draft_id = await generate_scheduled_draft(
                mode="signal",
                content="content",
                frequency="HUMAN",
                source_name="VOID",
                source_url="manual://vk/schedule/signal/test",
                platform="vk",
                semantic_theme="craft",
            )
        self.assertEqual(draft_id, 77)
        self.assertEqual(generate.call_count, 2)
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
