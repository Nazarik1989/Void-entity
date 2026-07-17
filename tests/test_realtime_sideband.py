import asyncio
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from realtime_sideband import SidebandControl, extract_response_text
from voice_hub_adapters import NazUnixPersonaAdapter, VoidSqlitePersonaAdapter


class DummyClient:
    pass


class RealtimeSidebandTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle_usage_and_transcript_are_server_collected(self) -> None:
        async def on_limit(_):
            return None

        control = SidebandControl(
            api_key="sk-server",
            hub_session_id="hub",
            call_id="rtc_1234567890",
            expected_realtime_session_id="sess_expected",
            instructions="server authority",
            voice="marin",
            deadline=2_000_000_000,
            on_limit=on_limit,
            on_end=on_limit,
            client_session=DummyClient(),
        )
        await control.handle_event({"type": "session.created", "session": {"id": "sess_expected"}})
        await control.handle_event(
            {
                "type": "conversation.item.done",
                "item": {"content": [{"transcript": "Фраза пользователя"}]},
            }
        )
        await control.handle_event(
            {
                "type": "response.done",
                "response": {"usage": {"total_tokens": 11, "input_tokens": 7, "output_tokens": 4}},
            }
        )
        self.assertEqual(control.transcript_summary(), "Фраза пользователя")
        self.assertEqual(control.snapshot()["usage"]["total_tokens"], 11)
        self.assertEqual(control.snapshot()["lifecycle"]["session.created"], 1)

    def test_response_text_extraction(self) -> None:
        self.assertEqual(
            extract_response_text({"output": [{"content": [{"type": "output_text", "text": "Итог"}]}]}),
            "Итог",
        )


class NazUnixContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_matches_naz_session_contract_and_accepts_duplicate(self) -> None:
        requests = []
        delivered_sessions = set()

        async def request(_path, payload):
            requests.append(payload)
            if payload["operation"] == "persona_instructions":
                return {"request_id": payload["request_id"], "ok": True, "instructions": "Naz"}
            self.assertEqual(
                set(payload),
                {"protocol", "request_id", "operation", "user_id", "session_id", "summary"},
            )
            saved = payload["session_id"] not in delivered_sessions
            delivered_sessions.add(payload["session_id"])
            return {
                "request_id": payload["request_id"],
                "ok": True,
                "receipt": f"naz:{payload['session_id']}",
                "saved": saved,
            }

        adapter = NazUnixPersonaAdapter("adapter.sock", request=request)
        self.assertEqual(await adapter.instructions("42"), "Naz")
        envelope = type(
            "Envelope",
            (),
            {
                "persona": "naz",
                "user_id": "42",
                "session_id": "server_session_key_123456789",
                "summary": "server summary",
            },
        )()
        first = await adapter.deliver(envelope)
        second = await adapter.deliver(envelope)
        self.assertEqual(first, "naz:server_session_key_123456789")
        self.assertEqual(second, first)
        self.assertEqual(
            set(requests[1]),
            {"protocol", "request_id", "operation", "user_id", "session_id", "summary"},
        )
        self.assertNotIn("idempotency_key", requests[1])
        self.assertEqual(requests[1]["user_id"], 42)
        self.assertNotIn("database", str(requests).casefold())

    async def test_adapter_rejects_response_without_boolean_saved_result(self) -> None:
        async def request(_path, payload):
            return {
                "request_id": payload["request_id"],
                "ok": True,
                "receipt": "naz:session",
            }

        adapter = NazUnixPersonaAdapter("adapter.sock", request=request)
        envelope = type(
            "Envelope",
            (),
            {
                "persona": "naz",
                "user_id": "42",
                "session_id": "server_session_key_123456789",
                "summary": "server summary",
            },
        )()
        with self.assertRaises(RuntimeError):
            await adapter.deliver(envelope)


class VoidAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_is_persisted_exactly_once_in_void_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "void.sqlite3"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute(
                    """
                    CREATE TABLE dialog_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()
            adapter = VoidSqlitePersonaAdapter(path, "VOID")
            envelope = type(
                "Envelope",
                (),
                {
                    "persona": "void",
                    "user_id": "42",
                    "session_id": "server_session_key_123456789",
                    "summary": "Серверный итог",
                },
            )()
            first = await adapter.deliver(envelope)
            second = await adapter.deliver(envelope)
            self.assertEqual(first, second)
            with closing(sqlite3.connect(path)) as conn:
                rows = conn.execute("SELECT user_id, role, content FROM dialog_messages").fetchall()
            self.assertEqual(rows, [(42, "memory", "Серверный итог")])
