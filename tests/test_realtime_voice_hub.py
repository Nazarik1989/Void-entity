import asyncio
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from realtime_voice_hub import (
    REALTIME_MODEL,
    AcceptedSummaryAdapter,
    ActiveSessionError,
    AuthenticationError,
    AuthorizationError,
    EphemeralToken,
    OpenAIRealtimeTokenProvider,
    SessionRegistry,
    TelegramLaunchVerifier,
    VkLaunchVerifier,
    VkVerificationNotConfigured,
    VoiceHubConfig,
    VoiceHubService,
)


BOT_TOKEN = "123456:test-token"


def telegram_launch_data(user_id: int, *, auth_date: int, token: str = BOT_TOKEN, extra=None) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AA-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    fields.update(extra or {})
    check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


class TelegramLaunchVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_800_000_000
        self.verifier = TelegramLaunchVerifier(BOT_TOKEN, max_age_seconds=300)

    def test_accepts_valid_fresh_launch_data(self) -> None:
        identity = self.verifier.verify(
            telegram_launch_data(42, auth_date=self.now - 10), now=self.now
        )
        self.assertEqual((identity.platform, identity.user_id), ("telegram", "42"))

    def test_rejects_tampering_and_stale_data(self) -> None:
        valid = telegram_launch_data(42, auth_date=self.now - 10)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(valid.replace("AA-test", "AA-tampered"), now=self.now)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(
                telegram_launch_data(42, auth_date=self.now - 301), now=self.now
            )

    def test_rejects_duplicate_fields(self) -> None:
        valid = telegram_launch_data(42, auth_date=self.now)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(valid + "&user=%7B%22id%22%3A43%7D", now=self.now)


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = []

    async def mint(self, *, persona: str, safety_identifier: str) -> EphemeralToken:
        self.calls.append((persona, safety_identifier))
        return EphemeralToken("ek_test_ephemeral", int(time.time()) + 60)


class CountingSummaryAdapter:
    def __init__(self) -> None:
        self.calls = []

    async def deliver(self, envelope) -> str:
        self.calls.append(envelope)
        await asyncio.sleep(0)
        return f"receipt:{envelope.idempotency_key}"


class VoiceHubSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.user_id = 424242
        self.config = VoiceHubConfig(
            openai_api_key="sk-server-only",
            telegram_bot_token=BOT_TOKEN,
            allowed_telegram_user_ids=frozenset({str(self.user_id)}),
            max_session_duration_seconds=120,
        )
        self.provider = FakeTokenProvider()
        self.naz_adapter = CountingSummaryAdapter()
        self.void_adapter = CountingSummaryAdapter()
        self.service = VoiceHubService(
            self.config,
            self.provider,
            verifiers={"telegram": TelegramLaunchVerifier(BOT_TOKEN)},
            summary_adapters={"naz": self.naz_adapter, "void": self.void_adapter},
            registry=SessionRegistry(),
        )

    def launch(self, user_id=None) -> str:
        return telegram_launch_data(user_id or self.user_id, auth_date=int(time.time()))

    async def test_server_allowlist_and_persona_allowlist_are_enforced_before_mint(self) -> None:
        with self.assertRaises(AuthorizationError):
            await self.service.start_session("telegram", self.launch(777777), "naz")
        with self.assertRaises(AuthorizationError):
            await self.service.start_session("telegram", self.launch(), "unknown")
        self.assertEqual(self.provider.calls, [])

    async def test_only_one_active_session_per_user(self) -> None:
        first = await self.service.start_session("telegram", self.launch(), "naz")
        self.assertEqual(first["model"], REALTIME_MODEL)
        self.assertEqual(first["ephemeral_token"], "ek_test_ephemeral")
        self.assertNotIn(self.config.openai_api_key, json.dumps(first))
        with self.assertRaises(ActiveSessionError):
            await self.service.start_session("telegram", self.launch(), "void")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(self.provider.calls[0][1].startswith("voice-hub-"))
        self.assertNotIn(str(self.user_id), self.provider.calls[0][1])

    async def test_summary_delivery_is_idempotent(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "void")
        args = (started["session_id"], "telegram", self.launch(), "Короткий итог")
        first, second = await asyncio.gather(
            self.service.finish_session(*args),
            self.service.finish_session(*args),
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.void_adapter.calls), 1)
        self.assertEqual(self.void_adapter.calls[0].idempotency_key, started["session_id"])

    async def test_finished_session_releases_user_slot(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "naz")
        await self.service.finish_session(
            started["session_id"], "telegram", self.launch(), "Итог"
        )
        replacement = await self.service.start_session("telegram", self.launch(), "void")
        self.assertEqual(replacement["persona"], "void")


class FixedModelTests(unittest.TestCase):
    def test_openai_payload_is_fixed_to_full_realtime_model(self) -> None:
        provider = OpenAIRealtimeTokenProvider("sk-test")
        payload = provider.session_payload("naz")
        self.assertEqual(payload["session"]["model"], "gpt-realtime-2.1")
        self.assertNotIn("mini", json.dumps(payload).casefold())

    def test_environment_cannot_override_model(self) -> None:
        env = {
            "OPENAI_REALTIME_MODEL": "gpt-realtime-mini",
            "OPENAI_VOICE_API_KEY": "sk-test",
            "BOT_TOKEN": BOT_TOKEN,
            "VOICE_HUB_ALLOWED_TELEGRAM_USER_IDS": "42",
        }
        with patch.dict(os.environ, env, clear=False), self.assertRaises(ValueError):
            VoiceHubConfig.from_env()

    def test_vk_verifier_does_not_invent_a_contract(self) -> None:
        with self.assertRaises(VkVerificationNotConfigured):
            VkLaunchVerifier().verify("opaque")
