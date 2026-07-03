import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import build_rubric_header, inject_rubric_header, too_much_english


class AutopostingRubricTests(unittest.TestCase):
    def test_build_rubric_header_for_news_mode(self) -> None:
        self.assertEqual(build_rubric_header("news", "AI"), "SIGNAL / AI")

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


if __name__ == "__main__":
    unittest.main()
