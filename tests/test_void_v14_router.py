import unittest
from unittest.mock import AsyncMock

from void_v14.config import VoidV14Config
from void_v14.router import VoidV14Router
from void_v14.schemas import (
    BudgetUsage,
    ConflictLevel,
    ExperimentalResult,
    ExperimentalState,
)


def experimental_result(response="v14 final"):
    return ExperimentalResult(
        trace_id="trace",
        state=ExperimentalState.COHERENT,
        agent_outputs=(),
        conflict_score=0.0,
        conflict_level=ConflictLevel.COHERENT,
        synthesis=response,
        confidence=0.8,
        warnings=(),
        suggested_action="send_synthesis",
        budget_usage=BudgetUsage(),
        rounds_used=0,
    )


class VoidV14RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_stable_mode_is_exact_old_route_and_never_calls_v14(self):
        engine = AsyncMock()
        stable = AsyncMock(return_value="stable exact response")
        router = VoidV14Router(config=VoidV14Config(), admin_id=7, engine=engine)
        outcome = await router.route(mode="stable", user_id=7, request="hello", stable_generate=stable)
        self.assertEqual(outcome.response, "stable exact response")
        stable.assert_awaited_once_with("hello", True)
        engine.run.assert_not_awaited()

    async def test_unknown_user_does_not_start_expensive_contour(self):
        engine = AsyncMock()
        stable = AsyncMock()
        router = VoidV14Router(config=VoidV14Config(), admin_id=7, engine=engine)
        outcome = await router.route(mode="experimental", user_id=99, request="hello", stable_generate=stable)
        self.assertFalse(outcome.allowed)
        stable.assert_not_awaited()
        engine.run.assert_not_awaited()

    async def test_experimental_uses_no_stable_memory_path(self):
        engine = AsyncMock()
        engine.run.return_value = experimental_result()
        stable = AsyncMock()
        router = VoidV14Router(config=VoidV14Config(), admin_id=7, engine=engine)
        outcome = await router.route(mode="experimental", user_id=7, request="hello", stable_generate=stable)
        self.assertEqual(outcome.response, "v14 final")
        self.assertFalse(outcome.persist_stable_after_send)
        stable.assert_not_awaited()

    async def test_hybrid_stable_candidate_is_preview_only(self):
        engine = AsyncMock()
        engine.run.return_value = experimental_result("hybrid final")
        stable = AsyncMock(return_value="stable candidate")
        router = VoidV14Router(config=VoidV14Config(), admin_id=7, engine=engine)
        outcome = await router.route(mode="hybrid", user_id=7, request="hello", stable_generate=stable)
        stable.assert_awaited_once_with("hello", False)
        engine.run.assert_awaited_once_with(
            user_id=7,
            request="hello",
            stable_candidate="stable candidate",
        )
        self.assertEqual(outcome.response, "hybrid final")
        self.assertTrue(outcome.persist_stable_after_send)


if __name__ == "__main__":
    unittest.main()
