import unittest

from void_v14.config import VoidV14Config
from void_v14.engine import classify_conflict, pairwise_conflict, VoidV14Engine
from void_v14.provider import FakeLLMProvider
from void_v14.schemas import (
    AgentOutput,
    BudgetUsage,
    Claim,
    ConflictLevel,
    ExperimentalState,
    ProviderSynthesisResult,
)


def output(agent, polarity, *, subject="decision", warning=""):
    return AgentOutput(
        agent=agent,
        claims=(Claim(subject, polarity, f"{agent} claim", 0.8),),
        assumptions=("bounded assumption",),
        risks=("bounded risk",),
        alternatives=("bounded alternative",),
        confidence=0.8,
        warnings=(warning,) if warning else (),
    )


class VoidV14EngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_coherent_fake_run_is_deterministic(self):
        config = VoidV14Config(agents=("a", "b"))
        provider = FakeLLMProvider({"a": output("a", 1), "b": output("b", 1)})
        engine = VoidV14Engine(provider, config)
        first = await engine.run(user_id=1, request="request", trace_id="trace-one")
        second = await engine.run(user_id=1, request="request", trace_id="trace-two")
        self.assertEqual(first.state, ExperimentalState.COHERENT)
        self.assertEqual(first.conflict_level, ConflictLevel.COHERENT)
        self.assertEqual(first.synthesis, second.synthesis)
        self.assertEqual(first.rounds_used, 0)

    async def test_state_c_is_reachable(self):
        config = VoidV14Config(agents=("a", "b"))
        provider = FakeLLMProvider({"a": output("a", -1), "b": output("b", 1)})
        result = await VoidV14Engine(provider, config).run(user_id=1, request="request")
        self.assertEqual(result.conflict_score, 1.0)
        self.assertEqual(result.state, ExperimentalState.COLLAPSE)
        self.assertEqual(result.suggested_action, "human_review")

    async def test_timeout_provider_failure_and_budget_are_safe(self):
        timeout_config = VoidV14Config(agents=("a",), timeout_seconds=0.01)
        timeout_provider = FakeLLMProvider({"a": output("a", 1)}, delay_seconds=0.05)
        timeout = await VoidV14Engine(timeout_provider, timeout_config).run(user_id=1, request="r")
        self.assertEqual(timeout.state, ExperimentalState.COLLAPSE)
        self.assertIn("provider_failure_or_timeout", timeout.warnings)

        failure_provider = FakeLLMProvider({"a": output("a", 1)}, fail_agents=frozenset({"a"}))
        failure = await VoidV14Engine(failure_provider, VoidV14Config(agents=("a",))).run(user_id=1, request="r")
        self.assertEqual(failure.suggested_action, "use_stable")

        expensive = FakeLLMProvider(
            {"a": output("a", 1), "b": output("b", 1)},
            usage=BudgetUsage(prompt_tokens=40, completion_tokens=40),
        )
        budget = await VoidV14Engine(
            expensive,
            VoidV14Config(agents=("a", "b"), token_budget=100),
        ).run(user_id=1, request="r")
        self.assertIn("budget_exhausted", budget.warnings)

        costly = FakeLLMProvider(
            {"a": output("a", 1)},
            usage=BudgetUsage(estimated_cost_usd=0.2),
        )
        cost_budget = await VoidV14Engine(
            costly,
            VoidV14Config(agents=("a",), cost_budget_usd=0.1),
        ).run(user_id=1, request="r")
        self.assertIn("budget_exhausted", cost_budget.warnings)

    async def test_synthesis_failure_returns_safe_result(self):
        provider = FakeLLMProvider({"a": output("a", 1)}, fail_synthesis=True)
        result = await VoidV14Engine(provider, VoidV14Config(agents=("a",))).run(user_id=1, request="r")
        self.assertEqual(result.state, ExperimentalState.COLLAPSE)
        self.assertIn("synthesis_failure_or_timeout", result.warnings)


class ConflictEngineTests(unittest.TestCase):
    def test_pairwise_divergence_excludes_self_comparison(self):
        single = pairwise_conflict((output("a", 1),))
        paired = pairwise_conflict((output("a", -1), output("b", 1)))
        self.assertEqual(single.comparisons, 0)
        self.assertEqual(paired.comparisons, 1)
        self.assertEqual(paired.score, 1.0)

    def test_collapse_threshold_is_checked_before_conflict(self):
        config = VoidV14Config(collapse_threshold=0.7, conflict_threshold=0.3)
        state, level = classify_conflict(0.8, config)
        self.assertEqual((state, level), (ExperimentalState.COLLAPSE, ConflictLevel.COLLAPSE))

    def test_madness_reentry_is_disabled_by_default(self):
        config = VoidV14Config()
        self.assertFalse(config.madness_reentry_enabled)
        self.assertEqual(config.max_rounds, 0)
        with self.assertRaises(ValueError):
            VoidV14Config(max_rounds=1)


if __name__ == "__main__":
    unittest.main()
