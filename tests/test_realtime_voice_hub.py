import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from realtime_voice_hub import (
    REALTIME_MODEL,
    ActiveSessionError,
    AuthenticationError,
    AuthorizationError,
    EphemeralToken,
    OpenAIRealtimeTokenProvider,
    TelegramLaunchVerifier,
    VkLaunchVerifier,
    VkVerificationNotConfigured,
    VoiceHubConfig,
    VoiceHubError,
    VoiceHubService,
    _validate_fields,
)
from voice_hub_store import VoiceHubStore


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

    def test_rejects_tampering_stale_and_duplicate_fields(self) -> None:
        valid = telegram_launch_data(42, auth_date=self.now - 10)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(valid.replace("AA-test", "AA-tampered"), now=self.now)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(telegram_launch_data(42, auth_date=self.now - 301), now=self.now)
        with self.assertRaises(AuthenticationError):
            self.verifier.verify(valid + "&user=%7B%22id%22%3A43%7D", now=self.now)


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = []

    async def mint(self, *, persona: str, instructions: str, safety_identifier: str) -> EphemeralToken:
        self.calls.append((persona, instructions, safety_identifier))
        return EphemeralToken("ek_test_ephemeral", int(time.time()) + 60, "sess_server_bound")


class RecordingAdapter:
    def __init__(self, persona: str) -> None:
        self.persona = persona
        self.calls = []

    async def instructions(self, user_id: str) -> str:
        return f"server instructions:{self.persona}:{user_id}"

    async def deliver(self, envelope) -> str:
        self.calls.append(envelope)
        await asyncio.sleep(0)
        return f"receipt:{envelope.session_id}"


class FakeControl:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.call_id = "rtc_1234567890"

    async def request_summary(self, *, timeout: float) -> str:
        self.events.append("summary")
        return "Серверный итог"

    def transcript_summary(self) -> str:
        self.events.append("transcript")
        return "Серверная расшифровка"

    async def hangup(self) -> None:
        self.events.append("hangup")

    def snapshot(self):
        return {"usage": {"total_tokens": 17}, "lifecycle": {"session.created": 1}}

    async def close(self) -> None:
        self.events.append("close")


class FakeSidebands:
    def __init__(self) -> None:
        self.controls = {}
        self.attach_calls = []
        self.events = []

    async def attach(self, **kwargs):
        self.attach_calls.append(kwargs)
        if kwargs["expected_realtime_session_id"] != "sess_server_bound":
            raise AssertionError("untrusted call was not bound to minted session")
        control = FakeControl(self.events)
        self.controls[kwargs["hub_session_id"]] = control
        return control

    def get(self, session_id):
        return self.controls.get(session_id)

    async def remove(self, session_id):
        control = self.controls.pop(session_id, None)
        if control:
            await control.close()

    async def hangup_call(self, call_id):
        self.events.append(f"hangup_call:{call_id}")

    async def close(self):
        self.controls.clear()


class RecoverySidebands(FakeSidebands):
    async def attach(self, **kwargs):
        raise RuntimeError("sideband is already closed")


class VoiceHubSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.user_id = 424242
        self.config = VoiceHubConfig(
            openai_api_key="sk-server-only",
            telegram_bot_token=BOT_TOKEN,
            allowed_telegram_user_ids=frozenset({str(self.user_id)}),
            max_session_duration_seconds=120,
            state_db_path=Path(self.tmp.name) / "hub.sqlite3",
        )
        self.provider = FakeTokenProvider()
        self.naz_adapter = RecordingAdapter("naz")
        self.void_adapter = RecordingAdapter("void")
        self.sidebands = FakeSidebands()
        self.store = VoiceHubStore(self.config.state_db_path)
        self.service = VoiceHubService(
            self.config,
            self.provider,
            verifiers={"telegram": TelegramLaunchVerifier(BOT_TOKEN)},
            persona_adapters={"naz": self.naz_adapter, "void": self.void_adapter},
            store=self.store,
            sidebands=self.sidebands,
        )

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    def launch(self, user_id=None) -> str:
        return telegram_launch_data(user_id or self.user_id, auth_date=int(time.time()))

    async def test_server_allowlists_are_enforced_before_mint(self) -> None:
        with self.assertRaises(AuthorizationError):
            await self.service.start_session("telegram", self.launch(777777), "naz")
        with self.assertRaises(AuthorizationError):
            await self.service.start_session("telegram", self.launch(), "unknown")
        self.assertEqual(self.provider.calls, [])

    async def test_one_active_session_is_persistent_and_key_stays_server_side(self) -> None:
        first = await self.service.start_session("telegram", self.launch(), "naz")
        self.assertEqual(first["model"], REALTIME_MODEL)
        self.assertNotIn(self.config.openai_api_key, json.dumps(first))
        with self.assertRaises(ActiveSessionError):
            await self.service.start_session("telegram", self.launch(), "void")
        second_service = VoiceHubService(
            self.config,
            FakeTokenProvider(),
            verifiers={"telegram": TelegramLaunchVerifier(BOT_TOKEN)},
            persona_adapters={"naz": self.naz_adapter, "void": self.void_adapter},
            store=VoiceHubStore(self.config.state_db_path),
            sidebands=FakeSidebands(),
        )
        with self.assertRaises(ActiveSessionError):
            await second_service.start_session("telegram", self.launch(), "void")
        self.assertNotIn(str(self.user_id), self.provider.calls[0][2])

    async def test_binding_uses_server_session_and_finish_uses_server_summary(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "void")
        await self.service.bind_call(
            started["session_id"], "telegram", self.launch(), "rtc_1234567890"
        )
        result = await self.service.finish_session(started["session_id"], "telegram", self.launch())
        self.assertEqual(result["status"], "finished")
        attach = self.sidebands.attach_calls[0]
        self.assertEqual(attach["expected_realtime_session_id"], "sess_server_bound")
        envelope = self.void_adapter.calls[0]
        self.assertEqual(envelope.user_id, str(self.user_id))
        self.assertEqual(envelope.session_id, started["session_id"])
        self.assertEqual(envelope.persona, "void")
        self.assertEqual(envelope.summary, "Серверный итог")
        self.assertEqual(self.sidebands.events[:2], ["summary", "hangup"])

    async def test_server_limit_hangs_up_before_summary_delivery(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "naz")
        await self.service.bind_call(
            started["session_id"], "telegram", self.launch(), "rtc_1234567890"
        )
        await self.service._finish_on_limit(started["session_id"])
        self.assertEqual(self.sidebands.events[:2], ["hangup", "transcript"])
        self.assertEqual(self.naz_adapter.calls[0].summary, "Серверная расшифровка")

    async def test_finish_is_idempotent(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "void")
        await self.service.bind_call(
            started["session_id"], "telegram", self.launch(), "rtc_1234567890"
        )
        args = (started["session_id"], "telegram", self.launch())
        first, second = await asyncio.gather(
            self.service.finish_session(*args), self.service.finish_session(*args)
        )
        self.assertEqual(first, second)
        self.assertEqual(len(self.void_adapter.calls), 1)

    async def test_recovery_hangs_up_before_delivering_saved_summary(self) -> None:
        started = await self.service.start_session("telegram", self.launch(), "void")
        await self.service.bind_call(
            started["session_id"], "telegram", self.launch(), "rtc_1234567890"
        )
        self.store.save_server_summary(started["session_id"], "Сохранённый серверный итог")
        recovery_sidebands = RecoverySidebands()
        recovery_service = VoiceHubService(
            self.config,
            FakeTokenProvider(),
            verifiers={"telegram": TelegramLaunchVerifier(BOT_TOKEN)},
            persona_adapters={"naz": self.naz_adapter, "void": self.void_adapter},
            store=VoiceHubStore(self.config.state_db_path),
            sidebands=recovery_sidebands,
        )
        await recovery_service.recover()
        self.assertEqual(
            recovery_sidebands.events,
            ["hangup_call:rtc_1234567890"],
        )
        self.assertEqual(self.store.get(started["session_id"]).state, "finished")


class FixedModelAndContractTests(unittest.TestCase):
    def test_openai_payload_is_fixed_to_full_realtime_model(self) -> None:
        provider = OpenAIRealtimeTokenProvider("sk-test")
        payload = provider.session_payload("naz", "authoritative")
        self.assertEqual(payload["session"]["model"], "gpt-realtime-2.1")
        self.assertEqual(payload["session"]["instructions"], "authoritative")
        self.assertNotIn("mini", json.dumps(payload).casefold())

    def test_client_authority_fields_are_rejected(self) -> None:
        for field in ("summary", "session_id", "user_id"):
            with self.subTest(field=field), self.assertRaises(VoiceHubError):
                _validate_fields(
                    {"platform": "telegram", "launch_data": "x", field: "client"},
                    required={"platform", "launch_data"},
                    allowed={"platform", "launch_data"},
                )

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
