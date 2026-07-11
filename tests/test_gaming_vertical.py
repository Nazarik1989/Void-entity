import unittest

import gaming_vertical as gaming


class GamingVerticalTests(unittest.TestCase):
    def test_characters_have_distinct_rubrics(self):
        naz = gaming.plan_gaming_content("naz", "новая механика", [])
        void = gaming.plan_gaming_content("void", "новая механика", [])
        self.assertNotEqual(naz["intent"], void["intent"])

    def test_cooldown_avoids_recent_shape(self):
        first = gaming.plan_gaming_content("void", "одна тема", [])
        recent = [{"facet": first["facet"], "content_format": first["content_format"]}]
        second = gaming.plan_gaming_content("void", "одна тема", recent)
        self.assertNotEqual(first["facet"], second["facet"])
        self.assertNotEqual(first["content_format"], second["content_format"])

    def test_void_prompt_preserves_character_and_commercial_boundary(self):
        plan = gaming.plan_gaming_content("void", "донат", [], commercial=True)
        prompt = gaming.prompt_context("void", plan)
        self.assertIn("взрослый наблюдатель", prompt)
        self.assertIn("серые сделки", prompt)
        self.assertIn("официальные creator-площадки", prompt)


if __name__ == "__main__":
    unittest.main()
