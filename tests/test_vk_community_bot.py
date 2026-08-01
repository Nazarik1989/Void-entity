from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

from vk_community_bot import (
    CommunityBot,
    EventStore,
    InboundMessage,
    Settings,
    VkApiClient,
    normalize_message,
    status_payload,
)
from void_dialog_adapter import DialogSettings, VoidDialogEngine


def make_settings(root: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "enabled": True,
        "group_id": 237593988,
        "allowed_group_ids": frozenset({237593988}),
        "allowed_user_ids": frozenset(),
        "token": "test-group-token",
        "api_version": "5.199",
        "state_db_path": root / "events.sqlite3",
        "health_path": root / "status.json",
        "long_poll_wait_seconds": 25,
        "http_timeout_seconds": 40,
        "rate_limit_count": 6,
        "rate_limit_window_seconds": 60,
        "max_text_chars": 4000,
        "max_reply_chars": 3500,
        "max_attempts": 5,
        "retry_base_seconds": 1,
        "processing_lease_seconds": 300,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def message_update(
    *,
    event_id: str = "event-1",
    group_id: int = 237593988,
    user_id: int = 42,
    peer_id: int | None = None,
    outgoing: int = 0,
    text: str = "Привет",
) -> dict:
    return {
        "type": "message_new",
        "event_id": event_id,
        "group_id": group_id,
        "object": {
            "message": {
                "from_id": user_id,
                "peer_id": user_id if peer_id is None else peer_id,
                "out": outgoing,
                "text": text,
            }
        },
    }


class SettingsTests(unittest.TestCase):
    def test_run_requires_explicit_enable_and_dedicated_group_token(self) -> None:
        environment = {
            "VK_COMMUNITY_BOT_GROUP_ID": "237593988",
            "VK_COMMUNITY_ALLOWED_GROUP_IDS": "237593988",
            "VK_GROUP_ACCESS_TOKEN": "secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "ENABLED"):
                Settings.from_env()
            os.environ["VK_COMMUNITY_BOT_ENABLED"] = "true"
            settings = Settings.from_env()
        self.assertEqual(settings.token, "secret")

    def test_target_must_be_the_single_allowlisted_community(self) -> None:
        environment = {
            "VK_COMMUNITY_BOT_ENABLED": "true",
            "VK_COMMUNITY_BOT_GROUP_ID": "237593988",
            "VK_COMMUNITY_ALLOWED_GROUP_IDS": "237593988,1",
            "VK_GROUP_ACCESS_TOKEN": "secret",
        }
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                Settings.from_env()


class NormalizationTests(unittest.TestCase):
    def test_accepts_only_private_inbound_message_for_allowlisted_group(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = make_settings(Path(folder))
            accepted = normalize_message(message_update(), settings)
            self.assertIsNotNone(accepted)
            self.assertEqual(accepted.user_id, 42)
            self.assertIsNone(
                normalize_message(message_update(group_id=1), settings)
            )
            self.assertIsNone(
                normalize_message(message_update(peer_id=2_000_000_042), settings)
            )
            self.assertIsNone(
                normalize_message(message_update(outgoing=1), settings)
            )

    def test_optional_user_allowlist_and_text_bound_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = make_settings(
                Path(folder), allowed_user_ids=frozenset({7}), max_text_chars=5
            )
            self.assertIsNone(normalize_message(message_update(user_id=8), settings))
            accepted = normalize_message(
                message_update(user_id=7, text="123456789"), settings
            )
            self.assertEqual(accepted.text, "12345")


class EventStoreTests(unittest.TestCase):
    def test_ingress_is_durable_and_event_id_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.sqlite3")
            inbound = InboundMessage(
                "same-event", 237593988, 42, 42, "hello", message_update()
            )
            self.assertEqual(store.ingest([inbound, inbound], now=100), 1)
            with closing(sqlite3.connect(store.path)) as connection:
                raw = json.loads(
                    connection.execute(
                        "SELECT raw_json FROM vk_community_events"
                    ).fetchone()[0]
                )
            self.assertEqual(
                raw,
                {
                    "event_id": "same-event",
                    "group_id": 237593988,
                    "peer_id": 42,
                    "user_id": 42,
                },
            )
            claimed = store.claim_next(now=100)
            self.assertEqual(claimed.event_id, "same-event")
            store.save_response("same-event", "reply")
            store.complete("same-event", now=101)
            self.assertIsNone(store.claim_next(now=102))
            self.assertEqual(store.counts()["done"], 1)

    def test_stale_processing_lease_recovers_generated_response(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.sqlite3")
            inbound = InboundMessage("event", 237593988, 42, 42, "hello", {})
            store.ingest([inbound], now=10)
            store.claim_next(now=10)
            store.save_response("event", "durable reply")
            recovered = store.claim_next(now=400, lease_seconds=300)
            self.assertEqual(recovered.response_text, "durable reply")

    def test_rate_limit_is_persistent_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            store = EventStore(Path(folder) / "events.sqlite3")
            self.assertEqual(
                store.rate_decision(42, limit=2, window_seconds=60, now=10),
                "allow",
            )
            self.assertEqual(
                store.rate_decision(42, limit=2, window_seconds=60, now=11),
                "allow",
            )
            self.assertEqual(
                store.rate_decision(42, limit=2, window_seconds=60, now=12),
                "notify",
            )
            self.assertEqual(
                store.rate_decision(42, limit=2, window_seconds=60, now=13),
                "drop",
            )
            self.assertEqual(
                store.rate_decision(42, limit=2, window_seconds=60, now=71),
                "allow",
            )


class DialogIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_vk_history_is_separate_and_character_db_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            character_db = root / "void.db"
            with closing(sqlite3.connect(character_db)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE character_states(
                        character_id TEXT PRIMARY KEY,
                        state_json TEXT NOT NULL,
                        core_version TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO character_states VALUES('void', ?, 'void-v1', 'now')",
                    (json.dumps({"facet": "companion"}),),
                )
            prompts: list[str] = []

            def response_create(**kwargs: object) -> str:
                prompts.append(str(kwargs["instructions"]))
                return "Ответ VOID"

            dialog_db = root / "vk-dialog.sqlite3"
            engine = VoidDialogEngine(
                DialogSettings(
                    db_path=dialog_db,
                    character_db_path=character_db,
                    api_key="",
                    base_url="",
                    model="test",
                ),
                response_create=response_create,
            )
            reply = await engine.generate(42, "Привет", platform="vk")
            self.assertEqual(reply, "Ответ VOID")
            self.assertIn("VOID", prompts[0])
            with closing(sqlite3.connect(dialog_db)) as connection:
                roles = connection.execute(
                    "SELECT role FROM dialog_messages ORDER BY id"
                ).fetchall()
                character_tables = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name='character_states'"
                ).fetchone()[0]
            self.assertEqual([row[0] for row in roles], ["user", "assistant", "memory"])
            self.assertEqual(character_tables, 0)
            with closing(sqlite3.connect(character_db)) as connection:
                dialog_tables = connection.execute(
                    "SELECT count(*) FROM sqlite_master WHERE name='dialog_messages'"
                ).fetchone()[0]
            self.assertEqual(dialog_tables, 0)


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, int]] = []

    async def get_long_poll_server(self) -> tuple[str, str, str]:
        return "https://lp.vk.com", "key", "1"

    async def poll(self, server: str, key: str, ts: str) -> dict:
        return {"ts": "2", "updates": []}

    async def send_message(self, user_id: int, text: str, random_id: int) -> int:
        self.sent.append((user_id, text, random_id))
        return 1


class FailOnceTransport(FakeTransport):
    async def send_message(self, user_id: int, text: str, random_id: int) -> int:
        self.sent.append((user_id, text, random_id))
        if len(self.sent) == 1:
            raise RuntimeError("transient")
        return 1


class CommunityBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_processing_reuses_generator_and_never_exposes_wall_method(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = make_settings(root)
            store = EventStore(settings.state_db_path)
            transport = FakeTransport()
            calls: list[tuple[int, str]] = []

            async def generate(user_id: int, text: str) -> str:
                calls.append((user_id, text))
                return "void reply"

            bot = CommunityBot(settings, store, generate, transport=transport)
            self.assertEqual(bot.ingest_updates([message_update(), message_update()]), 1)
            self.assertTrue(await bot.process_one())
            self.assertFalse(await bot.process_one())
            self.assertEqual(calls, [(42, "Привет")])
            self.assertEqual(transport.sent[0][:2], (42, "void reply"))
            self.assertGreater(transport.sent[0][2], 0)
            self.assertFalse(hasattr(transport, "wall_post"))

    async def test_generic_api_rejects_wall_post_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = make_settings(Path(folder))
            client = VkApiClient(settings, object())  # type: ignore[arg-type]
            with self.assertRaisesRegex(ValueError, "not allowlisted"):
                await client._api("wall.post", {})

    async def test_vk_send_uses_private_peer_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            settings = make_settings(Path(folder))
            client = VkApiClient(settings, object())  # type: ignore[arg-type]
            client._api = AsyncMock(return_value=123)  # type: ignore[method-assign]

            self.assertEqual(await client.send_message(42, "reply", 99), 123)

            client._api.assert_awaited_once_with(
                "messages.send",
                {"peer_id": 42, "random_id": 99, "message": "reply"},
            )

    async def test_send_retry_reuses_durable_reply_and_random_id(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = make_settings(root)
            store = EventStore(settings.state_db_path)
            transport = FailOnceTransport()
            generated: list[str] = []

            async def generate(user_id: int, text: str) -> str:
                generated.append(text)
                return "durable reply"

            bot = CommunityBot(settings, store, generate, transport=transport)
            bot.ingest_updates([message_update(event_id="retry-event")])
            self.assertTrue(await bot.process_one())
            with closing(sqlite3.connect(settings.state_db_path)) as connection, connection:
                connection.execute(
                    "UPDATE vk_community_events SET next_attempt_at=0 WHERE event_id='retry-event'"
                )
            self.assertTrue(await bot.process_one())
            self.assertEqual(generated, ["Привет"])
            self.assertEqual(len(transport.sent), 2)
            self.assertEqual(transport.sent[0], transport.sent[1])
            self.assertEqual(store.counts()["done"], 1)

    def test_health_status_becomes_stale(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = make_settings(root)
            settings.health_path.write_text(
                json.dumps(
                    {
                        "lifecycle": "running",
                        "updated_at": time.time() - 500,
                        "consecutive_poll_failures": 0,
                    }
                ),
                encoding="utf-8",
            )
            payload, healthy = status_payload(settings, max_age_seconds=120)
            self.assertFalse(healthy)
            self.assertGreater(payload["age_seconds"], 120)


if __name__ == "__main__":
    unittest.main()
