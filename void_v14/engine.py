"""Bounded multi-agent analysis and typed claim conflict detection."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass

from .config import VoidV14Config
from .memory import ExperimentalMemory
from .provider import LLMProvider
from .schemas import (
    AgentOutput,
    BudgetUsage,
    ConflictLevel,
    ExperimentalResult,
    ExperimentalState,
)


logger = logging.getLogger("void_v14.engine")


@dataclass(frozen=True, slots=True)
class ConflictAnalysis:
    score: float
    comparisons: int
    divergent_pairs: int


def pairwise_conflict(outputs: tuple[AgentOutput, ...]) -> ConflictAnalysis:
    """Compare only distinct agent pairs; self-comparisons are impossible."""
    divergences: list[float] = []
    for left_index in range(len(outputs)):
        for right_index in range(left_index + 1, len(outputs)):
            left = outputs[left_index]
            right = outputs[right_index]
            for left_claim in left.claims:
                for right_claim in right.claims:
                    if left_claim.subject_id != right_claim.subject_id:
                        continue
                    divergences.append(abs(left_claim.polarity - right_claim.polarity) / 2.0)
    if not divergences:
        return ConflictAnalysis(0.0, 0, 0)
    return ConflictAnalysis(
        score=round(sum(divergences) / len(divergences), 6),
        comparisons=len(divergences),
        divergent_pairs=sum(value > 0 for value in divergences),
    )


def classify_conflict(score: float, config: VoidV14Config) -> tuple[ExperimentalState, ConflictLevel]:
    # Order is intentional and regression-tested: COLLAPSE wins before CONFLICT.
    if score >= config.collapse_threshold:
        return ExperimentalState.COLLAPSE, ConflictLevel.COLLAPSE
    if score >= config.conflict_threshold:
        return ExperimentalState.CONFLICT, ConflictLevel.CONFLICT
    return ExperimentalState.COHERENT, ConflictLevel.COHERENT


class VoidV14Engine:
    def __init__(
        self,
        provider: LLMProvider,
        config: VoidV14Config,
        *,
        memory: ExperimentalMemory | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.memory = memory

    def _safe_result(
        self,
        trace_id: str,
        warning: str,
        *,
        outputs: tuple[AgentOutput, ...] = (),
        usage: BudgetUsage = BudgetUsage(),
        analysis: ConflictAnalysis = ConflictAnalysis(0.0, 0, 0),
    ) -> ExperimentalResult:
        return ExperimentalResult(
            trace_id=trace_id,
            state=ExperimentalState.COLLAPSE,
            agent_outputs=outputs,
            conflict_score=analysis.score,
            conflict_level=ConflictLevel.COLLAPSE,
            synthesis="Экспериментальный контур не завершил безопасный ответ. Используй stable VOID.",
            confidence=0.0,
            warnings=(warning,),
            suggested_action="use_stable",
            budget_usage=usage,
            rounds_used=0,
        )

    def _over_budget(self, usage: BudgetUsage) -> bool:
        return bool(
            usage.total_tokens > self.config.token_budget
            or usage.estimated_cost_usd > self.config.cost_budget_usd
        )

    async def run(
        self,
        *,
        user_id: int,
        request: str,
        stable_candidate: str = "",
        trace_id: str = "",
    ) -> ExperimentalResult:
        trace_id = trace_id or uuid.uuid4().hex
        logger.info("starting v14 trace metadata trace_id=%s user_id=%s", trace_id, user_id)
        per_call_tokens = max(1, self.config.token_budget // (len(self.config.agents) + 1))

        async def analyze(agent: str):
            return await asyncio.wait_for(
                self.provider.analyze(
                    request=request,
                    stable_candidate=stable_candidate,
                    agent=agent,
                    trace_id=trace_id,
                    max_tokens=per_call_tokens,
                ),
                timeout=self.config.timeout_seconds,
            )

        calls = await asyncio.gather(
            *(analyze(agent) for agent in self.config.agents),
            return_exceptions=True,
        )
        failures = [item for item in calls if isinstance(item, BaseException)]
        successful = [item for item in calls if not isinstance(item, BaseException)]
        outputs = tuple(item.output for item in successful)
        usage = BudgetUsage()
        for item in successful:
            usage = usage.plus(item.usage)
        if failures:
            result = self._safe_result(trace_id, "provider_failure_or_timeout", outputs=outputs, usage=usage)
            return self._finish(user_id, result)
        if self._over_budget(usage):
            result = self._safe_result(trace_id, "budget_exhausted", outputs=outputs, usage=usage)
            return self._finish(user_id, result)

        analysis = pairwise_conflict(outputs)
        state, level = classify_conflict(analysis.score, self.config)
        remaining = max(1, self.config.token_budget - usage.total_tokens)
        try:
            synthesis = await asyncio.wait_for(
                self.provider.synthesize(
                    request=request,
                    stable_candidate=stable_candidate,
                    outputs=outputs,
                    trace_id=trace_id,
                    max_tokens=min(per_call_tokens, remaining),
                ),
                timeout=self.config.timeout_seconds,
            )
        except Exception:
            result = self._safe_result(
                trace_id,
                "synthesis_failure_or_timeout",
                outputs=outputs,
                usage=usage,
                analysis=analysis,
            )
            return self._finish(user_id, result)
        usage = usage.plus(synthesis.usage)
        if self._over_budget(usage):
            result = self._safe_result(
                trace_id, "budget_exhausted", outputs=outputs, usage=usage, analysis=analysis
            )
            return self._finish(user_id, result)

        warnings = tuple(dict.fromkeys(
            warning
            for item in outputs
            for warning in item.warnings
        )) + tuple(synthesis.warnings)
        result = ExperimentalResult(
            trace_id=trace_id,
            state=state,
            agent_outputs=outputs,
            conflict_score=analysis.score,
            conflict_level=level,
            synthesis=synthesis.synthesis,
            confidence=max(0.0, min(1.0, synthesis.confidence)),
            warnings=warnings,
            suggested_action=("human_review" if state is ExperimentalState.COLLAPSE else synthesis.suggested_action),
            budget_usage=usage,
            rounds_used=0,
        )
        return self._finish(user_id, result)

    def _finish(self, user_id: int, result: ExperimentalResult) -> ExperimentalResult:
        if self.memory is not None:
            self.memory.record_result(user_id, result)
        logger.info("finished v14 trace metadata trace_id=%s user_id=%s state=%s", result.trace_id, user_id, result.state.value)
        return result
