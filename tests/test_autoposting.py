import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import (
    VOID_TO_NAZ_FORBIDDEN_OPENINGS,
    VOID_TO_NAZ_OPENING_OPTIONS,
    build_void_to_naz_exchange_payload,
    build_rubric_header,
    clean_source_lines,
    display_source_name,
    inject_rubric_header,
    too_much_english,
    trim_post,
    validate_void_fragment_for_naz,
)
from void_core import CONTENT_PLAN


class AutopostingRubricTests(unittest.TestCase):
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

        self.assertEqual(payload["exchange_kind"], "private_dialogue_fragment")
        self.assertEqual(payload["text"], fragment)
        self.assertFalse(payload["ready_to_publish"])
        self.assertTrue(payload["requires_adaptation"])
        self.assertEqual(payload["adaptation_role"], "naz_interpretation_after_void_voice")

    def test_void_to_naz_openings_have_backstage_variety(self) -> None:
        self.assertIn("Из тёмного угла прилетело:", VOID_TO_NAZ_OPENING_OPTIONS)
        self.assertGreaterEqual(len(VOID_TO_NAZ_OPENING_OPTIONS), 6)
        self.assertIn(
            "Void опять говорит странно, но по делу:",
            VOID_TO_NAZ_FORBIDDEN_OPENINGS,
        )


if __name__ == "__main__":
    unittest.main()
