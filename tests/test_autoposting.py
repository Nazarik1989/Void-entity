import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import build_rubric_header, clean_source_lines, display_source_name, inject_rubric_header, too_much_english, trim_post
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


if __name__ == "__main__":
    unittest.main()
