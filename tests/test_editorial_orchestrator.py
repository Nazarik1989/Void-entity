import inspect
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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
    persona_pool_sizes = {axis: len(values) for axis, values in pools.items()}
    persona_pool_sizes.update({"rubric": 7, "source_ref": 7, "content_format": 2, "production_mode": 2})
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
        persona_pool_sizes=persona_pool_sizes,
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
    def test_cooldown_rounds_each_persona_wide_axis_pool(self):
        expected = {3: 2, 6: 4, 8: 5, 13: 8, 17: 10, 36: 22}
        self.assertEqual(
            {size: eo.cooldown_depth(size) for size in expected},
            expected,
        )

    def test_constrained_exhaustion_uses_persona_wide_depth_and_true_lru(self):
        history = (
            {"axis": "oldest-compatible"},
            {"axis": "unrelated"},
            {"axis": "newest-compatible"},
        )
        chosen = eo._choose(
            plan_id="deterministic-plan",
            axis="axis",
            values=("oldest-compatible", "newest-compatible"),
            history=history,
            persona_wide_pool_size=6,
        )
        self.assertEqual(chosen, "oldest-compatible")

    def test_one_eligible_rubric_and_source_keep_full_seven_item_depth(self):
        rubric_rows = [
            {
                "key": f"rubric-{index}",
                "name": f"Rubric {index}",
                "mode": "signal",
                "brief": "bounded observation",
            }
            for index in range(7)
        ]
        runtime = void_editorial_catalog.build_context(
            platform="telegram",
            slot="12:00",
            seed="one-of-seven",
            rubric_rows=rubric_rows,
            source_rows=[
                {
                    "source_ref": "catalog:only-compatible",
                    "topic": "one compatible observation",
                    "rubric_keys": ("rubric-6",),
                }
            ],
            published_history=(),
            character=character_state.CharacterState(),
            persona_pool_sizes={"rubric": 7, "source_ref": 7},
        )
        plan = eo.plan_release(runtime)
        self.assertEqual(runtime.persona_pool_sizes["rubric"], 7)
        self.assertEqual(runtime.persona_pool_sizes["source_ref"], 7)
        self.assertEqual(eo.cooldown_depth(runtime.persona_pool_sizes["rubric"]), 4)
        self.assertEqual(plan.rubric, "Rubric 6")

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

    def test_transitive_scheduled_call_graph_is_orchestrated_and_v14_free(self):
        def reachable(root):
            pending = [root]
            found = set()
            allowed_modules = {
                "main", "void_vk_producer", "editorial_orchestrator",
                "void_editorial_catalog", "vk_publish_queue", "vk_queue_consumer",
            }
            while pending:
                function = pending.pop()
                if function in found or not inspect.isfunction(function):
                    continue
                if function.__module__ not in allowed_modules:
                    continue
                found.add(function)
                for name in function.__code__.co_names:
                    value = function.__globals__.get(name)
                    if inspect.isfunction(value):
                        pending.append(value)
                    elif inspect.ismodule(value):
                        nested = getattr(value, name, None)
                        if inspect.isfunction(nested):
                            pending.append(nested)
                        for nested_name in function.__code__.co_names:
                            nested = getattr(value, nested_name, None)
                            if inspect.isfunction(nested):
                                pending.append(nested)
            return found

        forbidden = {
            main.semantic_gate_decision,
            main.choose_scheduled_rubric,
            main.choose_telegram_schedule_slot,
            main.build_crosspost_from_naz_sync,
            main.void_v14_command,
            main.build_void_v14_router,
        }
        for root in (main.auto_loop, main.exchange_loop, void_vk_producer.produce_scheduled):
            graph = reachable(root)
            self.assertIn(eo.plan_release, graph, root.__name__)
            self.assertFalse(graph & forbidden, root.__name__)

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
            main.record_release_observability(
                plan,
                slot_captured_at="2026-07-22T12:00:00+03:00",
                generation_package_status="accepted",
            )
            draft_id = main.save_draft(
                "signal", "Draft", "x" * 300, "VOID", "manual://void/test", "HUMAN", 8,
                plan_id=plan.plan_id,
                editorial_plan=plan.to_dict(),
                generation_package={},
            )
            self.assertEqual(main.get_recent_content_signatures(), [])
            self.assertTrue(
                main.mark_published(
                    draft_id,
                    telegram_chat_id="channel-receipt",
                    telegram_message_id=123,
                )
            )
            self.assertEqual(len(main.get_recent_content_signatures()), 1)
            published = main.get_draft(draft_id)
            self.assertEqual(published["telegram_chat_id"], "channel-receipt")
            self.assertEqual(published["telegram_message_id"], 123)
            self.assertEqual(published["history_commit_status"], "inserted")
            observed = main.get_release_observability(plan.plan_id, "telegram")
            self.assertEqual(observed["telegram_message_id"], 123)
            self.assertEqual(observed["history_commit_status"], "inserted")
            main.record_content_signature(plan.to_dict(), plan.topic, draft_id)
            main.record_content_signature(plan.to_dict(), plan.topic, draft_id)
            self.assertEqual(len(main.get_recent_content_signatures()), 1)

    def test_crosspost_observability_keeps_both_destinations_but_one_history_spend(self):
        telegram_plan = eo.plan_release(context(crosspost_plan_id="shared-plan-destinations-0001"))
        vk_plan = replace(telegram_plan, platform="vk")
        with tempfile.TemporaryDirectory() as root, patch.object(
            main, "DB_PATH", str(Path(root) / "void.sqlite3")
        ):
            main.init_db()
            for plan in (telegram_plan, vk_plan):
                main.record_release_observability(
                    plan,
                    slot_captured_at="2026-07-22T12:00:00+03:00",
                    generation_package_status="accepted",
                )
            telegram_draft = main.save_draft(
                "signal", "TG", "x" * 300, plan_id=telegram_plan.plan_id,
                editorial_plan=telegram_plan.to_dict(),
            )
            vk_draft = main.save_draft(
                "signal", "VK", "x" * 300, plan_id=vk_plan.plan_id,
                editorial_plan=vk_plan.to_dict(),
            )
            self.assertTrue(main.mark_published(
                telegram_draft, telegram_chat_id="channel", telegram_message_id=11
            ))
            self.assertTrue(main.mark_published(
                vk_draft, vk_job_id="void-job", vk_receipt_id="void-job"
            ))
            telegram_observed = main.get_release_observability(
                telegram_plan.plan_id, "telegram"
            )
            vk_observed = main.get_release_observability(vk_plan.plan_id, "vk")
            self.assertEqual(telegram_observed["telegram_message_id"], 11)
            self.assertEqual(telegram_observed["vk_receipt_id"], "")
            self.assertEqual(vk_observed["vk_receipt_id"], "void-job")
            self.assertIsNone(vk_observed["telegram_message_id"])
            self.assertEqual(telegram_observed["history_commit_status"], "inserted")
            self.assertEqual(vk_observed["history_commit_status"], "already_recorded")
            self.assertEqual(len(main.get_recent_content_signatures()), 1)

    def test_delivery_claim_is_stale_safe_and_never_retries_ambiguous_send(self):
        with tempfile.TemporaryDirectory() as root, patch.object(
            main, "DB_PATH", str(Path(root) / "void.sqlite3")
        ):
            main.init_db()
            before_draft = "stale-before-draft-plan-0001"
            self.assertEqual(
                main.claim_editorial_delivery(before_draft, "telegram"), "claimed"
            )
            conn = main.db()
            conn.execute(
                """
                UPDATE editorial_delivery_state
                SET attempt_started_at='2000-01-01T00:00:00+00:00'
                WHERE plan_id=? AND destination='telegram'
                """,
                (before_draft,),
            )
            conn.commit()
            conn.close()
            self.assertEqual(
                main.claim_editorial_delivery(
                    before_draft, "telegram", stale_seconds=1
                ),
                "claimed",
            )

            ambiguous = "stale-after-draft-plan-0001"
            self.assertEqual(main.claim_editorial_delivery(ambiguous, "telegram"), "claimed")
            draft_id = main.save_draft(
                "signal", "Draft", "x" * 300, plan_id=ambiguous
            )
            main.attach_editorial_delivery_draft(ambiguous, "telegram", draft_id)
            conn = main.db()
            conn.execute(
                """
                UPDATE editorial_delivery_state
                SET attempt_started_at='2000-01-01T00:00:00+00:00'
                WHERE plan_id=? AND destination='telegram'
                """,
                (ambiguous,),
            )
            conn.commit()
            conn.close()
            self.assertEqual(
                main.claim_editorial_delivery(
                    ambiguous, "telegram", stale_seconds=1
                ),
                "failed",
            )
            self.assertEqual(main.get_recent_content_signatures(), [])

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
    async def test_duplicate_crosspost_plan_delivers_once_and_spends_history_once(self):
        package = eo.GenerationPackage(
            final_text="x" * 400,
            concrete_scene="One used object under narrow light.",
            visual_subject="The same used object.",
            visual_relation_to_thesis="Its wear carries the observation.",
            image_prompt_seed="Used stone under one narrow light.",
            track_tags=("calm", "dark"),
        )
        plan_id = "duplicate-crosspost-plan-0001"
        sends: list[int] = []

        async def confirmed_publish(_bot, draft_id):
            sends.append(draft_id)
            main.mark_published(
                draft_id,
                telegram_chat_id="void-channel",
                telegram_message_id=9000 + len(sends),
            )
            return f"Опубликовано: #{draft_id}. Картинок: 0"

        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(main, "DB_PATH", str(Path(root) / "void.sqlite3")),
            patch.object(main, "CROSSPOST_EXCHANGE_DIR", Path(root) / "exchange"),
            patch.object(main, "CROSSPOST_EXCHANGE_ENABLED", True),
            patch.object(main, "CROSSPOST_EXCHANGE_AUTO_PUBLISH", True),
            patch.object(main, "CROSSPOST_EXCHANGE_MAX_PER_RUN", 5),
            patch.object(
                main, "generate_scheduled_package", new=AsyncMock(return_value=package)
            ) as generate,
            patch.object(main, "publish_draft", new=AsyncMock(side_effect=confirmed_publish)) as publish,
        ):
            main.init_db()
            main.ensure_exchange_dirs()
            for suffix in ("one", "two"):
                payload = {
                    "id": f"exchange-{suffix}",
                    "plan_id": plan_id,
                    "source": "naz_ai_bot",
                    "publish_mode": "auto",
                    "topic": "automation workflow",
                    "text": "A private automation observation long enough for processing.",
                }
                (main.exchange_dir("naz_to_void", "inbox") / f"{suffix}.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )

            await main.process_naz_to_void_exchange(object())

            self.assertEqual(len(sends), 1)
            self.assertEqual(publish.await_count, 1)
            self.assertEqual(generate.await_count, 1)
            self.assertEqual(len(main.get_recent_content_signatures()), 1)
            delivery = main.get_editorial_delivery_state(plan_id, "telegram")
            self.assertEqual(delivery["state"], "committed")
            self.assertEqual(delivery["draft_id"], sends[0])
            self.assertEqual(
                len(list(main.exchange_dir("naz_to_void", "processed").glob("*.json"))), 2
            )

    async def test_crosspost_uses_one_plan_package_and_never_stores_private_source(self):
        package = eo.GenerationPackage(
            final_text="x" * 400,
            concrete_scene="One used object under narrow light.",
            visual_subject="The same used object.",
            visual_relation_to_thesis="Its wear carries the observation.",
            image_prompt_seed="Used stone under one narrow light.",
            track_tags=("calm", "dark"),
        )
        private_sentinel = "PRIVATE-TOKEN-SENTINEL"
        declared_sentinel = "DECLARED-SUMMARY-SENTINEL"
        payload = {
            "id": "exchange-item-0001",
            "plan_id": "shared-crosspost-plan-0001",
            "topic": "automation workflow",
            "public_summary": f"automation {declared_sentinel}",
            "public_summary_safe": True,
        }
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(main, "DB_PATH", str(Path(root) / "void.sqlite3")),
            patch.object(main, "generate_scheduled_package", new=AsyncMock(return_value=package)) as generate,
        ):
            main.init_db()
            draft_id, plan = await main.create_planned_naz_crosspost_draft(
                payload,
                f"private automation detail {private_sentinel}",
                fallback_id="fallback",
            )
            draft = main.get_draft(draft_id)
            stored = " ".join(
                (
                    str(draft["editorial_plan_json"]),
                    str(draft["generation_package_json"]),
                )
            )
            self.assertNotIn(private_sentinel, stored)
            self.assertNotIn(declared_sentinel, stored)
            self.assertEqual(plan.plan_id, payload["plan_id"])
            self.assertEqual(draft["generation_package_status"], "accepted")
            self.assertEqual(draft["image_qa_status"], "not_run")
            observed = main.get_release_observability(plan.plan_id, "telegram")
            self.assertEqual(observed["generation_package_status"], "accepted")
            self.assertEqual(observed["image_qa_status"], "not_run")
            self.assertEqual(observed["draft_id"], draft_id)
            self.assertEqual(main.get_recent_content_signatures(), [])
            self.assertNotIn(private_sentinel, generate.call_args.kwargs["source_material"])
            self.assertNotIn(declared_sentinel, generate.call_args.kwargs["source_material"])

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
