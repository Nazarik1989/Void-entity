import hashlib
import json
import unittest

import editorial_policy as policy
import main
from vk_publish_queue import METADATA_FIELDS


RUBRICS = {"Culture", "Frequency", "Midnight", "MATERIAL / МАТЕРИЯ", "approved_backstage", "canonical_story"}


def make_brief(**overrides):
    values = {
        "destination": "telegram",
        "scheduled_slot": "telegram:culture",
        "source_type": "scheduled_rubric",
        "source_reference": "schedule:2026-07-21:culture",
        "rubric": "Culture",
        "thesis": "A used object carries the choices that shaped it.",
        "context_reason": "The configured culture slot selected this semantic axis.",
        "visual_subject": "one worn paper object on dark stone",
        "visual_relation": "wear and local light make the accumulated choices visible",
        "allowed_rubrics": RUBRICS,
        "required_elements": ("worn paper",),
        "music_required": False,
    }
    values.update(overrides)
    return policy.build_brief(**values)


class VoidEditorialPolicyTests(unittest.TestCase):
    def test_void_rubric_instruction_snapshots(self):
        variants = (
            make_brief(),
            make_brief(rubric="Frequency", scheduled_slot="telegram:frequency", source_reference="schedule:frequency"),
            make_brief(rubric="Midnight", scheduled_slot="telegram:midnight", source_reference="schedule:midnight"),
            make_brief(rubric="MATERIAL / МАТЕРИЯ", destination="vk", scheduled_slot="vk:material", source_reference="schedule:material", music_required=True),
        )
        hashes = [hashlib.sha256(policy.render_text_instructions(item, "VOID canonical persona").encode()).hexdigest() for item in variants]
        self.assertEqual(hashes, [
            "ee6602401df734be4427d7bcc580a89f702ddfca4ada431692e6c694be32612f",
            "f818d20df048300c8361f97b7937c8e280202e639548f54b659d89b7d00abb12",
            "8ff053cfb8dde57daeffc17eafd7a5875b0ad86dfab213a15f232ed15d6e825e",
            "2b66075848b60d4194ffe9f6e665f60b557158975588c412ec6e6f305cf47be1",
        ])

    def test_visual_snapshot_has_void_not_naz_identity(self):
        brief = make_brief()
        compiled = policy.render_visual_instructions(brief, main.VOID_VISUAL_CANON_PROMPT)
        self.assertEqual(hashlib.sha256(compiled.encode()).hexdigest(), "54a4dbfbf806a01061542a46c242136c81c0625ebea666803384c644418ff949")
        self.assertIn("Naz blue ultraviolet identity", compiled)
        self.assertIn("No people", compiled)

    def test_backstage_story_and_current_event_require_typed_source(self):
        backstage = make_brief(source_type="approved_backstage_seed", source_reference="backstage:approved:2", rubric="approved_backstage")
        story = make_brief(source_type="canonical_story", source_reference="story:canonical:4", rubric="canonical_story")
        event = make_brief(source_type="current_event_with_source", source_reference="https://example.test/source")
        self.assertEqual({backstage.source_type, story.source_type, event.source_type}, {"approved_backstage_seed", "canonical_story", "current_event_with_source"})

    def test_untyped_random_missing_source_and_conflict_fail_closed(self):
        with self.assertRaises(policy.BriefValidationError):
            make_brief(source_type="random")
        with self.assertRaises(policy.BriefValidationError):
            make_brief(source_reference="")
        with self.assertRaises(policy.BriefValidationError):
            make_brief(required_elements=("same",), forbidden_elements=("same",))

    def test_people_and_generic_symbols_are_forbidden_by_default(self):
        brief = make_brief()
        forbidden = " ".join(brief.forbidden_elements).casefold()
        self.assertFalse(brief.people_allowed)
        self.assertIn("elderly", forbidden)
        self.assertIn("humanoid robot", forbidden)
        self.assertIn("glowing sphere", forbidden)
        with self.assertRaises(policy.BriefValidationError):
            make_brief(people_allowed=True, allowed_people_description="elderly observer")

    def test_image_unrelated_to_thesis_is_rejected(self):
        raw = json.dumps({"accepted": False, "reason_code": "image_thesis_mismatch",
            "literal_description": "a random robot in a neon interface", "subject_matches": False,
            "thesis_supported": False, "unexplained_people": False, "unexplained_elements": True,
            "visual_bible_matches": False, "why_here": True})
        decision = policy.parse_image_gate_response(raw)
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_code, "image_thesis_mismatch")

    def test_text_gate_schema_enumerates_codes_and_excludes_visual_policy(self):
        brief = make_brief()
        payload = json.loads(policy.build_text_gate_prompt(brief, "fixture candidate"))
        self.assertEqual(
            set(policy.text_gate_response_schema()["properties"]["reason_code"]["enum"]),
            set(policy.TEXT_GATE_REASON_CODES),
        )
        self.assertNotIn("visual_subject", payload["brief"])
        self.assertNotIn("required_elements", payload["brief"])

    def test_text_gate_parser_classifies_unknown_reason_code(self):
        payload = {
            "accepted": False,
            "reason_code": "topic_mismatch",
            "entry_context_clear": True,
            "self_contained": True,
            "invented_current_event": False,
            "topic_matches": False,
            "persona_matches": True,
        }
        with self.assertRaises(policy.GateResponseError) as raised:
            policy.parse_text_gate_response(json.dumps(payload))
        self.assertEqual(raised.exception.reason_code, "schema_unknown_reason_code")
        self.assertEqual(raised.exception.field_names, ("reason_code",))

    def test_job_metadata_has_policy_versions_without_prompt_or_text(self):
        metadata = make_brief(destination="vk", scheduled_slot="vk:culture", music_required=True).job_metadata()
        self.assertEqual(set(metadata), METADATA_FIELDS)
        self.assertNotIn("thesis", metadata)
        self.assertNotIn("visual_subject", metadata)


if __name__ == "__main__":
    unittest.main()
