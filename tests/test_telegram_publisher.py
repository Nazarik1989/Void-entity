import os
import sys
import unittest
from unittest.mock import ANY, AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import (
    DEFAULT_OPENAI_IMAGE_MODEL,
    TelegramPostPackage,
    build_image_prompts_sync,
    generate_post_images_sync,
    image_count_for_draft,
    image_visual_directions,
    publish_draft,
    send_telegram_post,
)
from void_core import (
    MATERIAL_RUBRIC,
    VOID_CANONICAL_MATERIALS,
    VOID_CANONICAL_PALETTE,
    VOID_VISUAL_CANON_PROMPT,
)


class TelegramPublisherTests(unittest.IsolatedAsyncioTestCase):
    def test_primary_image_model_is_exact_openrouter_gpt_image_2_id(self) -> None:
        self.assertEqual(DEFAULT_OPENAI_IMAGE_MODEL, "openai/gpt-image-2")

    def test_visual_directions_rotate_avatar_and_people_free_scenes(self) -> None:
        directions = [image_visual_directions(draft_id, 1)[0] for draft_id in range(6)]

        self.assertTrue(any("canonical avatar" in direction for direction in directions))
        self.assertTrue(any("no people" in direction.lower() for direction in directions))
        self.assertEqual(len(directions), len(set(directions)))

    def test_visual_canon_contains_exact_palette_and_materials(self) -> None:
        self.assertEqual(
            VOID_CANONICAL_PALETTE,
            {
                "Absolute Black": "#000000",
                "Coal Black": "#080808",
                "Graphite": "#171717",
                "Smoke": "#2A2A2A",
                "Ash Grey": "#696966",
                "Bone White": "#E8E6DF",
                "Pure White": "#FFFFFF",
            },
        )
        normalized = " ".join(VOID_VISUAL_CANON_PROMPT.split())
        for name, value in VOID_CANONICAL_PALETTE.items():
            self.assertIn(f"{name} {value}", normalized)
        for material in VOID_CANONICAL_MATERIALS:
            self.assertIn(material, normalized)

    def test_visual_canon_constrains_darkness_white_and_naz_code(self) -> None:
        canon = " ".join(VOID_VISUAL_CANON_PROMPT.casefold().split())
        self.assertIn("80–90%", canon)
        self.assertIn("2–5%", canon)
        self.assertIn("one source of light", canon)
        for forbidden in (
            "no bright blue, purple, or neon identity",
            "no data networks",
            "circuit diagrams",
            "code",
            "digital interfaces",
            "energy rings",
            "technological glow",
        ):
            self.assertIn(forbidden, canon)

    def test_visual_canon_blocks_luxury_cyberpunk_and_gothic_cliches(self) -> None:
        canon = " ".join(VOID_VISUAL_CANON_PROMPT.casefold().split())
        for forbidden in (
            "demonstrative luxury",
            "gold decoration",
            "glossy advertising interiors",
            "supercars",
            "bright cyberpunk",
            "mystical runes",
            "occult clichés",
            "skulls",
            "ravens",
            "generic gothic imagery",
            "excessive grain",
            "large quotations",
        ):
            self.assertIn(forbidden, canon)

    def test_material_builds_complete_four_frame_sequence_with_canon(self) -> None:
        draft = {
            "id": 20,
            "mode": "material",
            "title": "Material study",
            "post": "A worn stone object.",
            "source_name": "VOID",
        }

        with patch("main.call_ai", return_value="IMAGE: first composition only"):
            prompts = build_image_prompts_sync(draft)

        self.assertEqual(image_count_for_draft("material", ""), 4)
        self.assertEqual(len(prompts), 4)
        for index, prompt in enumerate(prompts, start=1):
            self.assertIn(VOID_VISUAL_CANON_PROMPT, prompt)
            self.assertIn("MATERIAL / МАТЕРИЯ", prompt)
            self.assertIn(f"Frame {index} of 4", prompt)
        self.assertEqual(MATERIAL_RUBRIC["duration_seconds"], (12, 20))
        self.assertEqual(MATERIAL_RUBRIC["frame_count"], (3, 4))
        self.assertFalse(MATERIAL_RUBRIC["scheduled"])

    def test_two_image_prompts_receive_distinct_mandatory_directions(self) -> None:
        draft = {
            "id": 10,
            "mode": "digest",
            "title": "Test signal",
            "post": "Concrete topic",
            "source_name": "Test source",
        }

        with patch("main.call_ai", return_value="IMAGE: first composition\nIMAGE: second composition"):
            prompts = build_image_prompts_sync(draft)

        self.assertEqual(len(prompts), 2)
        self.assertIn("Mandatory visual direction:", prompts[0])
        self.assertIn("Mandatory visual direction:", prompts[1])
        self.assertNotEqual(prompts[0], prompts[1])

    def test_image_model_failure_is_diagnostic_and_not_silently_substituted(self) -> None:
        client = MagicMock()
        client.images.generate.side_effect = RuntimeError("model unavailable")

        with (
            patch("main.OPENAI_IMAGE_MODEL", "openai/gpt-image-2"),
            patch("main.openai_client", return_value=client),
            patch("main.build_image_prompts_sync", return_value=["prompt"]),
            patch("builtins.print") as log,
        ):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                generate_post_images_sync({})

        client.images.generate.assert_called_once_with(
            model="openai/gpt-image-2",
            prompt="prompt",
            size=ANY,
            quality=ANY,
            n=1,
        )
        diagnostic = log.call_args.args[0]
        self.assertIn("model=openai/gpt-image-2", diagnostic)
        self.assertIn("source-image/text-only fallback", diagnostic)

    def make_bot(self, calls: list[str]) -> MagicMock:
        bot = MagicMock()

        async def send_photo(**kwargs):
            calls.append("send_photo")

        async def send_media_group(**kwargs):
            calls.append("send_media_group")

        async def send_message(**kwargs):
            calls.append("send_message")

        bot.send_photo = AsyncMock(side_effect=send_photo)
        bot.send_media_group = AsyncMock(side_effect=send_media_group)
        bot.send_message = AsyncMock(side_effect=send_message)
        return bot

    async def test_single_image_is_sent_before_full_text(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)
        package = TelegramPostPackage(text="Полный текст", draft_id=7, images=(b"image-one",))

        outcome = await send_telegram_post(bot, package)

        self.assertTrue(outcome.success)
        self.assertEqual(calls, ["send_photo", "send_message"])
        self.assertNotIn("caption", bot.send_photo.await_args.kwargs)
        self.assertEqual(bot.send_message.await_args.kwargs["text"], "Полный текст")

    async def test_media_group_is_sent_before_full_text(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)
        package = TelegramPostPackage(
            text="Полный текст",
            draft_id=8,
            images=(b"image-one", b"image-two"),
        )

        outcome = await send_telegram_post(bot, package)

        self.assertTrue(outcome.success)
        self.assertEqual(calls, ["send_media_group", "send_message"])
        for item in bot.send_media_group.await_args.kwargs["media"]:
            self.assertIsNone(item.caption)

    async def test_media_error_stops_before_text(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)

        async def fail_photo(**kwargs):
            calls.append("send_photo")
            raise RuntimeError("Telegram rejected media")

        bot.send_photo.side_effect = fail_photo
        package = TelegramPostPackage(text="Полный текст", draft_id=9, images=(b"image",))

        outcome = await send_telegram_post(bot, package)

        self.assertFalse(outcome.success)
        self.assertEqual(calls, ["send_photo"])
        bot.send_message.assert_not_awaited()

    async def test_text_only_is_sent_when_no_image_exists(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)
        package = TelegramPostPackage(
            text="Полный текст",
            draft_id=10,
            no_image_reason="all image fallbacks unavailable",
        )

        with patch("builtins.print") as log:
            outcome = await send_telegram_post(bot, package)

        self.assertTrue(outcome.success)
        self.assertEqual(calls, ["send_message"])
        log.assert_called_once()
        self.assertIn("all image fallbacks unavailable", log.call_args.args[0])

    async def test_partial_failure_does_not_mark_publication_state(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)

        async def fail_text(**kwargs):
            calls.append("send_message")
            raise RuntimeError("Telegram rejected text")

        bot.send_message.side_effect = fail_text
        draft = {
            "id": 11,
            "post": "Полный текст публикации",
            "source_url": "",
        }
        package = TelegramPostPackage(text=draft["post"], draft_id=11, images=(b"image",))

        with (
            patch("main.CHANNEL_ID", "@void"),
            patch("main.get_draft", return_value=draft),
            patch("main.quality_check", return_value=(True, "")),
            patch("main.prepare_telegram_post_package", AsyncMock(return_value=package)),
            patch("main.mark_published") as mark_published,
            patch("main.apply_character_event") as apply_character_event,
            patch("main.record_content_signature") as record_content_signature,
            patch("main.set_setting") as set_setting,
        ):
            result = await publish_draft(
                bot,
                11,
                content_plan={"platform": "telegram"},
                content_topic="test",
                apply_planned_character_event=True,
                setting_updates={"telegram_void_recent": "SIGNAL"},
            )

        self.assertEqual(calls, ["send_photo", "send_message"])
        self.assertTrue(result.startswith("Публикация не выполнена:"))
        mark_published.assert_not_called()
        apply_character_event.assert_not_called()
        record_content_signature.assert_not_called()
        set_setting.assert_not_called()

    async def test_media_failure_does_not_mark_published_or_send_text(self) -> None:
        calls: list[str] = []
        bot = self.make_bot(calls)

        async def fail_photo(**kwargs):
            calls.append("send_photo")
            raise RuntimeError("Telegram rejected media")

        bot.send_photo.side_effect = fail_photo
        draft = {
            "id": 12,
            "mode": "news",
            "post": "Полный текст публикации",
            "source_url": "",
        }
        package = TelegramPostPackage(text=draft["post"], draft_id=12, images=(b"image",))

        with (
            patch("main.CHANNEL_ID", "@void"),
            patch("main.get_draft", return_value=draft),
            patch("main.quality_check", return_value=(True, "")),
            patch("main.prepare_telegram_post_package", AsyncMock(return_value=package)),
            patch("main.mark_published") as mark_published,
            patch("main.apply_character_event") as apply_character_event,
        ):
            result = await publish_draft(bot, 12)

        self.assertEqual(calls, ["send_photo"])
        bot.send_message.assert_not_awaited()
        self.assertTrue(result.startswith("Публикация не выполнена:"))
        mark_published.assert_not_called()
        apply_character_event.assert_not_called()


if __name__ == "__main__":
    unittest.main()
