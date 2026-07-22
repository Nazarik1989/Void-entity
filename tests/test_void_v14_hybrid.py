import asyncio
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import main
from void_v14.config import VoidV14Config
from void_v14.engine import VoidV14Engine
from void_v14.memory import ExperimentalMemory
from void_v14.provider import FakeLLMProvider
from void_v14.router import RouteOutcome, VoidV14Router
from void_v14.schemas import AgentOutput, Claim
from tests.test_void_v14_router import experimental_result


def output(agent):
    return AgentOutput(
        agent=agent,
        claims=(Claim("subject", 1, f"{agent} agrees"),),
        assumptions=(),
        risks=(),
        alternatives=(),
        confidence=0.8,
        warnings=(),
    )


class VoidV14HybridTests(unittest.IsolatedAsyncioTestCase):
    def test_example_environment_keeps_v14_disabled(self):
        example = Path(".env.example").read_text(encoding="utf-8")
        self.assertIn("VOID_V14_ENABLED=false", example)

    async def test_parallel_users_have_isolated_trace_memory(self):
        with tempfile.TemporaryDirectory() as root:
            memory = ExperimentalMemory(Path(root) / "v14.sqlite3", retention_days=30)
            config = VoidV14Config(agents=("a", "b"))
            provider = FakeLLMProvider({"a": output("a"), "b": output("b")}, delay_seconds=0.01)
            engine = VoidV14Engine(provider, config, memory=memory)
            first, second = await asyncio.gather(
                engine.run(user_id=1, request="first", trace_id="trace-user-one"),
                engine.run(user_id=2, request="second", trace_id="trace-user-two"),
            )
            self.assertIsNotNone(memory.get_trace(1, first.trace_id))
            self.assertIsNone(memory.get_trace(2, first.trace_id))
            self.assertIsNotNone(memory.get_trace(2, second.trace_id))
            self.assertIsNone(memory.get_trace(1, second.trace_id))

    def test_scheduled_routes_never_reference_v14(self):
        for function in (
            main.publish_telegram_void_scheduled_once,
            main.create_planned_scheduled_draft,
            main.make_scheduled_rubric_draft_once,
        ):
            self.assertNotIn("void_v14", inspect.getsource(function))

    def test_voice_and_realtime_modules_are_not_v14_dependencies(self):
        for module_name in ("realtime_voice_hub.py", "voice_hub_adapters.py", "realtime_sideband.py"):
            source = (Path(main.__file__).parent / module_name).read_text(encoding="utf-8")
            self.assertNotIn("void_v14", source)

    async def test_main_stable_command_never_builds_v14(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            text="/v14 stable hello",
            answer=AsyncMock(),
        )
        with (
            patch.object(main, "ADMIN_ID", 7),
            patch.object(main, "VOID_V14_ENABLED", True),
            patch.object(main, "generate_dialog_answer", new=AsyncMock(return_value="stable final")) as stable,
            patch.object(main, "build_void_v14_router") as build,
        ):
            await main.void_v14_command(message)
        stable.assert_awaited_once_with(7, "hello")
        build.assert_not_called()
        message.answer.assert_awaited_once()

    async def test_hybrid_saves_only_sent_final_after_success(self):
        engine = AsyncMock()
        engine.run.return_value = experimental_result("hybrid final")
        router = VoidV14Router(config=VoidV14Config(), admin_id=7, engine=engine)
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            text="/v14 hybrid hello",
            answer=AsyncMock(),
        )
        with (
            patch.object(main, "ADMIN_ID", 7),
            patch.object(main, "VOID_V14_ENABLED", True),
            patch.object(main, "build_void_v14_router", return_value=router),
            patch.object(main, "generate_dialog_answer", new=AsyncMock(return_value="stable preview")) as stable,
            patch.object(main, "persist_dialog_turn") as persist,
        ):
            await main.void_v14_command(message)
        stable.assert_awaited_once_with(7, "hello", persist=False)
        persist.assert_called_once_with(7, "hello", "hybrid final")
        message.answer.assert_awaited_once()

    async def test_failed_hybrid_send_never_persists_final(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            answer=AsyncMock(side_effect=RuntimeError("send failed")),
        )
        outcome = RouteOutcome("hybrid", "hybrid final", True, persist_stable_after_send=True)
        with patch.object(main, "persist_dialog_turn") as persist:
            with self.assertRaisesRegex(RuntimeError, "send failed"):
                await main.deliver_void_v14_outcome(message, outcome, "hello")
        persist.assert_not_called()

    async def test_default_off_blocks_admin_before_router_construction(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            text="/v14 experimental hello",
            answer=AsyncMock(),
        )
        with (
            patch.object(main, "ADMIN_ID", 7),
            patch.object(main, "VOID_V14_ENABLED", False),
            patch.object(main, "build_void_v14_router") as build,
        ):
            await main.void_v14_command(message)
        build.assert_not_called()

    async def test_enabled_flag_does_not_authorize_contact_or_old_allowlist(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=99),
            text="/v14 experimental hello",
            answer=AsyncMock(),
        )
        with (
            patch.object(main, "ADMIN_ID", 7),
            patch.object(main, "VOID_V14_ENABLED", True),
            patch.dict("os.environ", {"VOID_V14_ALLOWLIST": "99"}),
            patch.object(main, "build_void_v14_router") as build,
        ):
            await main.void_v14_command(message)
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
