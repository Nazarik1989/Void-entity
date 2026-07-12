import unittest

import character_state as character


class VoidCharacterStateTests(unittest.TestCase):
    def test_events_change_state_without_changing_core(self) -> None:
        state = character.CharacterState()
        original_core = state.core_version
        for event in ("noise", "naz_challenge", "human_story", "beauty", "publish"):
            state = character.apply_event(state, event)

        self.assertEqual(state.core_version, original_core)
        self.assertIn(state.facet, character.FACETS)
        for axis in ("energy", "warmth", "tension", "curiosity", "confidence", "sociability"):
            self.assertGreaterEqual(getattr(state, axis), 0)
            self.assertLessEqual(getattr(state, axis), 100)

    def test_human_cost_activates_guardian_without_making_void_anti_tech(self) -> None:
        state = character.CharacterState(tension=62, warmth=70)
        state = character.apply_event(state, "human_cost")
        self.assertEqual(state.facet, "guardian")
        self.assertIn("not anti-technology", " ".join(character.CORE_TRUTHS))

    def test_naz_challenge_brings_motion_back_to_void(self) -> None:
        state = character.CharacterState()
        changed = character.apply_event(state, "naz_challenge")
        self.assertGreater(changed.energy, 42)
        self.assertGreater(changed.curiosity, 68)

    def test_planner_respects_recent_shape_cooldowns(self) -> None:
        recent = [{
            "intent": "наблюдать",
            "format": "тихое наблюдение",
            "hook": "деталь",
            "media": "кинематографический кадр",
            "facet": "observer",
        }] * 4
        plan = character.plan_content(character.CharacterState(), recent, topic="digital noise", platform="telegram")
        self.assertNotEqual(plan["intent"], "наблюдать")
        self.assertNotEqual(plan["format"], "тихое наблюдение")
        self.assertNotEqual(plan["hook"], "деталь")
        self.assertNotEqual(plan["media"], "кинематографический кадр")

    def test_prompt_allows_void_to_be_wise_without_becoming_infallible(self) -> None:
        state = character.CharacterState()
        plan = character.plan_content(state, [], topic="test", platform="telegram")
        prompt = character.prompt_context(state, plan)
        self.assertIn("не против технологий", prompt)
        self.assertIn("не всезнающий гуру", prompt)
        self.assertIn("не обязан выигрывать спор с Naz", prompt)

    def test_admin_axis_correction_is_clamped_and_reselects_facet(self) -> None:
        state = character.set_axis(character.CharacterState(), "tension", 140)
        self.assertEqual(state.tension, 100)
        self.assertEqual(state.facet, "guardian")


if __name__ == "__main__":
    unittest.main()
