import unittest

import character_state
import content_formats
import duo_relationship


class DuoRelationshipTests(unittest.TestCase):
    def test_private_thought_cannot_be_marked_as_already_published(self) -> None:
        payload = duo_relationship.build_private_thought_payload(
            speaker="naz",
            thought="Если инструмент нельзя проверить руками, я пока не готов называть его революцией.",
            topic="инструменты",
            relationship=duo_relationship.RelationshipState(),
        )
        payload["already_published"] = True
        ok, reason = duo_relationship.validate_private_thought_payload(payload)
        self.assertFalse(ok)
        self.assertIn("already published", reason)

    def test_void_reflection_can_mention_naz_without_becoming_repost(self) -> None:
        relationship = duo_relationship.RelationshipState()
        payload = duo_relationship.build_private_thought_payload(
            speaker="naz",
            thought="Если инструмент нельзя проверить руками, я пока не готов называть его революцией.",
            topic="инструменты",
            relationship=relationship,
        )
        brief = duo_relationship.reflection_brief(
            receiver="void", payload=payload, relationship=relationship,
            receiver_character_context="VOID context",
        )
        self.assertIn("После разговора с Naz", brief)
        self.assertIn("never copy the thought verbatim", brief)

    def test_relationship_keeps_warmth_during_disagreement(self) -> None:
        state = duo_relationship.apply_event(
            duo_relationship.RelationshipState(), "disagreement", topic="скорость против смысла"
        )
        self.assertGreater(state.warmth, 50)
        self.assertIn("скорость против смысла", state.unresolved_topics)

    def test_simulation_does_not_mutate_saved_state(self) -> None:
        state = character_state.CharacterState()
        original = state.to_dict()
        plans = character_state.simulate(state, [], count=8)
        self.assertEqual(state.to_dict(), original)
        self.assertEqual(len(plans), 8)

    def test_conversation_format_requires_private_context(self) -> None:
        chosen = content_formats.choose_format([], platform="telegram", energy=80, seed_key="a")
        self.assertNotEqual(chosen["key"], "dialogue_reflection")

    def test_original_reflection_passes_copy_guard(self) -> None:
        source = "Если инструмент нельзя проверить руками, я пока не готов называть его революцией."
        result = (
            "МЫСЛИ ПОСЛЕ РАЗГОВОРА\n\nNaz снова потребовал дать ему отвёртку. "
            "Пожалуй, это полезная привычка: обещания будущего лучше слышны после встречи с реальностью."
        )
        ok, reason = duo_relationship.reflection_is_original(source, result)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
