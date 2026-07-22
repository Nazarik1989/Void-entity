import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import character_state
import editorial_orchestrator as eo
import main
import void_vk_producer
import void_editorial_catalog
import vk_publish_queue


AXES = (
    "thesis_direction", "epistemic_state", "tension", "semantic_theme", "facet",
    "author_role", "emotional_arc", "reader_relation", "structure", "hook",
    "ending", "energy", "seriousness", "tempo", "length", "humor", "imagery",
    "visual_mode", "visual_subject_direction", "visual_relation", "track_tags",
)


def context(*, seed="seed-0", history=(), crosspost_plan_id=""):
    pools = {axis: tuple(f"{axis}-{index}" for index in range(3)) for axis in AXES}
    pools["thesis_direction"] = tuple(f"thesis-{index}" for index in range(17))
    pools["semantic_theme"] = ("theme-a", "theme-b", "theme-c")
    pools["visual_subject_direction"] = (
        "one worn stone object revealed by a narrow light",
        "one used paper object at the visible-hidden boundary",
        "one smoked-glass object carrying a real trace of time",
    )
    pools["visual_relation"] = (
        "the light reveals only the physical fact carrying the thesis",
        "the object's wear makes the planned claim visible",
        "the threshold embodies the exact selected tension",
    )
    pools["track_tags"] = ("calm,dark", "ambient,memory", "material,organic")
    return eo.EditorialContext(
        persona="void",
        platform="telegram",
        slot="20:00",
        seed=seed,
        sources=(
            eo.EditorialSource("catalog:one", "A concrete observation", rubric_keys=("signal",)),
            eo.EditorialSource("catalog:two", "Another observation", rubric_keys=("signal",)),
            eo.EditorialSource("catalog:three", "A third observation", rubric_keys=("signal",)),
        ),
        rubrics=(eo.EditorialRubric("signal", "SIGNAL / VOID", "signal", "one weighted observation"),),
        pools=pools,
        semantic_cards={
            "theme-a": ("a-1", "a-2"),
            "theme-b": ("b-1", "b-2"),
            "theme-c": ("c-1", "c-2"),
        },
        published_history=tuple(history),
        policy_versions={"content": "c1", "visual": "v1", "music": "m1"},
        crosspost_plan_id=crosspost_plan_id,
    )


class EditorialOrchestratorTests(unittest.TestCase):
    def test_contract_and_required_cooldown(self):
        self.assertEqual(eo.cooldown_depth(17), 10)
        plan = eo.plan_release(context())
        self.assertEqual(set(plan.to_dict()), set(eo.EditorialPlan.__dataclass_fields__))

    def test_fifty_offline_plans_respect_cooldown_and_compatibility(self):
        history = []
        cards = context().semantic_cards
        for index in range(50):
            plan = eo.plan_release(context(seed=f"simulation-{index}", history=history))
            self.assertNotIn(plan.thesis_direction, [item["thesis_direction"] for item in history[-10:]])
            self.assertIn(plan.semantic_card, cards[plan.semantic_theme])
            self.assertEqual(plan.production_mode, "standard")
            history.append(plan.to_dict())
        self.assertEqual(len(history), 50)

    def test_fifty_plans_from_runtime_catalog_are_compatible(self):
        rubric_rows = []
        source_rows = []
        for index, rubric in enumerate(main.TELEGRAM_VOID_SCHEDULE):
            row = dict(rubric)
            key = void_editorial_catalog.rubric_key(str(row["name"]))
            row["key"] = key
            rubric_rows.append(row)
            is_news = str(row.get("voice")) == "news"
            source_rows.append(
                {
                    "source_ref": f"https://example.test/{index}" if is_news else f"void-catalog:{index}",
                    "topic": str(row.get("brief") or row["name"]),
                    "source_type": "documented_source" if is_news else "catalog",
                    "rubric_keys": (key,),
                    "source_verified": is_news,
                }
            )
        history = []
        for index in range(50):
            runtime = void_editorial_catalog.build_context(
                platform="telegram",
                slot="simulation",
                seed=f"runtime-{index}",
                rubric_rows=rubric_rows,
                source_rows=source_rows,
                published_history=history,
                character=character_state.CharacterState(),
            )
            plan = eo.plan_release(runtime)
            self.assertIn(plan.semantic_card, runtime.semantic_cards[plan.semantic_theme])
            self.assertEqual(plan.production_mode, "standard")
            history.append(plan.to_dict())
        self.assertEqual(len(history), 50)

    def test_diversity_exhaustion_and_crosspost_contract(self):
        first = eo.plan_release(context(seed="one"))
        second = eo.plan_release(context(seed="two", history=(first.to_dict(),)))
        exhausted = eo.plan_release(context(seed="three", history=(first.to_dict(), second.to_dict())))
        self.assertTrue(exhausted.plan_id)
        crosspost = eo.plan_release(context(crosspost_plan_id="crosspost-plan-0001"))
        self.assertEqual(crosspost.plan_id, "crosspost-plan-0001")

    def test_text_visual_and_music_share_one_plan(self):
        plan = eo.plan_release(context())
        package = eo.GenerationPackage(
            final_text="x" * 500,
            concrete_scene="A narrow light reveals wear on one stone object.",
            visual_subject="The same worn stone object.",
            visual_relation_to_thesis="The wear makes the planned claim visible.",
            image_prompt_seed="One worn stone in darkness under a narrow light.",
            track_tags=plan.track_tags,
        )
        prompt = eo.generation_prompt(plan, persona_direction="VOID direction")
        visual = eo.package_visual_brief(plan, package)
        self.assertIn(plan.plan_id, prompt)
        self.assertIn(plan.plan_id, visual)
        self.assertEqual(package.track_tags, plan.track_tags)

    def test_runtime_visual_reads_stored_plan_package_without_visual_model_call(self):
        plan = eo.plan_release(context())
        package = eo.GenerationPackage(
            final_text="x" * 500,
            concrete_scene="A narrow light reveals wear on one stone object.",
            visual_subject="The same worn stone object.",
            visual_relation_to_thesis="The wear makes the planned claim visible.",
            image_prompt_seed="One worn stone in darkness under a narrow light.",
            track_tags=plan.track_tags,
        )
        with tempfile.TemporaryDirectory() as root, patch.object(main, "DB_PATH", str(Path(root) / "void.sqlite3")):
            main.init_db()
            draft_id = main.save_draft(
                "signal", "Draft", package.final_text, "VOID", "manual://void/test", "HUMAN", 8,
                plan_id=plan.plan_id,
                editorial_plan=plan.to_dict(),
                generation_package=main.generation_package_dict(package),
            )
            with patch.object(main, "call_ai") as visual_model:
                prompts = main.build_image_prompts_sync(main.get_draft(draft_id))
            visual_model.assert_not_called()
            self.assertTrue(prompts)
            self.assertTrue(all(plan.plan_id in item for item in prompts))

    def test_diag_and_generic_people_are_rejected(self):
        plan = eo.plan_release(context())
        payload = {
            "final_text": "DIAG: traceback " + "x" * 500,
            "concrete_scene": "A concrete dark room and one object.",
            "visual_subject": "A generic person in darkness.",
            "visual_relation_to_thesis": "The object relates to the thesis.",
            "image_prompt_seed": "A generic person and stock scene.",
            "track_tags": list(plan.track_tags),
        }
        with self.assertRaises(eo.GenerationPackageError):
            eo.parse_generation_package(json.dumps(payload), plan)

    def test_migrated_routes_do_not_call_legacy_selectors(self):
        forbidden = (
            "choose_scheduled_rubric(", "choose_telegram_schedule_slot(",
            "semantic_gate_decision(", "build_character_directive(", "random.choice(",
        )
        for function in (
            main.create_planned_scheduled_draft,
            main.publish_telegram_void_scheduled_once,
            main.make_scheduled_rubric_draft_once,
        ):
            source = inspect.getsource(function)
            for token in forbidden:
                self.assertNotIn(token, source, f"{function.__name__}: {token}")
        self.assertIn("create_planned_scheduled_draft(", inspect.getsource(main.publish_telegram_void_scheduled_once))

    def test_vk_receipt_is_the_only_place_that_commits_planned_history(self):
        source = inspect.getsource(void_vk_producer.sync_published_drafts)
        self.assertIn("mark_published", source)
        self.assertNotIn("record_content_signature", source)
        self.assertNotIn("record_content_signature", inspect.getsource(void_vk_producer.enqueue_draft))
        mark_source = inspect.getsource(main.mark_published)
        self.assertIn("_record_content_signature_conn", mark_source)
        self.assertLess(mark_source.index("published_now"), mark_source.index("_record_content_signature_conn"))

    def test_draft_is_not_history_and_crosspost_plan_is_counted_once(self):
        plan = eo.plan_release(context())
        with tempfile.TemporaryDirectory() as root, patch.object(main, "DB_PATH", str(Path(root) / "void.sqlite3")):
            main.init_db()
            draft_id = main.save_draft(
                "signal", "Draft", "x" * 300, "VOID", "manual://void/test", "HUMAN", 8,
                plan_id=plan.plan_id,
                editorial_plan=plan.to_dict(),
                generation_package={},
            )
            self.assertEqual(main.get_recent_content_signatures(), [])
            self.assertTrue(main.mark_published(draft_id))
            self.assertEqual(len(main.get_recent_content_signatures()), 1)
            main.record_content_signature(plan.to_dict(), plan.topic, draft_id)
            main.record_content_signature(plan.to_dict(), plan.topic, draft_id)
            self.assertEqual(len(main.get_recent_content_signatures()), 1)

    def test_queue_accepts_safe_plan_metadata_and_legacy_jobs(self):
        legacy = vk_publish_queue.build_job(
            producer="void", target_group_id="1", text="post", media=[], track_query="track",
            dedupe_key="legacy-job", source_ref="void:draft:1",
        )
        planned = vk_publish_queue.build_job(
            producer="void", target_group_id="1", text="post", media=[], track_query="track",
            dedupe_key="planned-job", source_ref="void:draft:2", plan_id="planned-release-0001",
            editorial={"persona": "void", "track_tags": ["calm", "dark"]},
        )
        self.assertNotIn("plan_id", legacy)
        self.assertEqual(planned["plan_id"], "planned-release-0001")
        self.assertEqual(planned["editorial"]["persona"], "void")


class GenerationRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_keeps_plan_id_and_all_axes(self):
        plan = eo.plan_release(context())
        valid = json.dumps(
            {
                "final_text": "x" * 500,
                "concrete_scene": "A narrow light reveals wear on one stone object.",
                "visual_subject": "The exact same worn stone object.",
                "visual_relation_to_thesis": "The wear demonstrates the selected thesis.",
                "image_prompt_seed": "One worn stone in darkness under a narrow light.",
                "track_tags": list(plan.track_tags),
            }
        )
        model = MagicMock(side_effect=["not-json", valid])
        with patch.object(main, "call_ai", model), patch.object(main, "quality_check", return_value=(True, "ok")):
            package = await main.generate_scheduled_package(plan, character_state.CharacterState())
        self.assertEqual(package.track_tags, plan.track_tags)
        self.assertEqual(model.call_count, 2)
        first = model.call_args_list[0].args[1]
        second = model.call_args_list[1].args[1]
        self.assertIn("Источник:", first)
        self.assertIn("Источник:", second)
        for _, value in plan.to_dict().items():
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
            self.assertIn(rendered, first)
            self.assertIn(rendered, second)


if __name__ == "__main__":
    unittest.main()
