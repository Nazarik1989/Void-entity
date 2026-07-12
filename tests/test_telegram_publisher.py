import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from main import TelegramPostPackage, publish_draft, send_telegram_post


class TelegramPublisherTests(unittest.IsolatedAsyncioTestCase):
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
