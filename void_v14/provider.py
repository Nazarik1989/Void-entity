"""Async providers for VOID v14. Tests use only FakeLLMProvider."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Protocol

from .config import VoidV14Config
from .schemas import (
    AgentOutput,
    BudgetUsage,
    ProviderAgentResult,
    ProviderSynthesisResult,
)

try:  # The laboratory remains importable when the optional SDK is absent.
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - exercised only in minimal deployments
    AsyncOpenAI = None  # type: ignore[assignment]


class LLMProvider(Protocol):
    async def analyze(
        self,
        *,
        request: str,
        stable_candidate: str,
        agent: str,
        trace_id: str,
        max_tokens: int,
    ) -> ProviderAgentResult: ...

    async def synthesize(
        self,
        *,
        request: str,
        stable_candidate: str,
        outputs: tuple[AgentOutput, ...],
        trace_id: str,
        max_tokens: int,
    ) -> ProviderSynthesisResult: ...


def _json_object(raw: str) -> Mapping[str, Any]:
    text = str(raw or "").strip().replace("```json", "").replace("```", "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("provider returned no JSON object")
    value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("provider JSON must be an object")
    return value


def _usage(response: Any, config: VoidV14Config) -> BudgetUsage:
    usage = getattr(response, "usage", None)
    prompt_tokens = max(0, int(getattr(usage, "input_tokens", 0) or 0))
    completion_tokens = max(0, int(getattr(usage, "output_tokens", 0) or 0))
    estimated_cost = (
        prompt_tokens * config.input_cost_per_million_usd
        + completion_tokens * config.output_cost_per_million_usd
    ) / 1_000_000
    return BudgetUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=round(estimated_cost, 8),
    )


def _transient(exc: Exception) -> bool:
    name = type(exc).__name__.casefold()
    return any(marker in name for marker in ("timeout", "ratelimit", "connection", "serviceunavailable"))


class OpenAIProvider:
    """Inactive-by-default async OpenAI adapter with bounded transient retries."""

    def __init__(
        self,
        *,
        config: VoidV14Config,
        api_key: str,
        model: str,
        base_url: str = "",
        client: Any | None = None,
    ) -> None:
        if client is None:
            if AsyncOpenAI is None:
                raise RuntimeError("OpenAI SDK is unavailable")
            if not api_key:
                raise RuntimeError("VOID v14 provider key is not configured")
            kwargs: dict[str, Any] = {"api_key": api_key}
            if base_url:
                kwargs["base_url"] = base_url
            client = AsyncOpenAI(**kwargs)
        self._client = client
        self._model = model
        self._config = config
        self._semaphore = asyncio.Semaphore(config.concurrency)

    async def _complete(self, instructions: str, prompt: str, max_tokens: int) -> tuple[str, BudgetUsage]:
        for attempt in range(self._config.transient_retries + 1):
            try:
                async with self._semaphore:
                    response = await asyncio.wait_for(
                        self._client.responses.create(
                            model=self._model,
                            instructions=instructions,
                            input=prompt,
                            max_output_tokens=max_tokens,
                        ),
                        timeout=self._config.timeout_seconds,
                    )
                return str(response.output_text or "").strip(), _usage(response, self._config)
            except Exception as exc:
                if attempt >= self._config.transient_retries or not _transient(exc):
                    raise
        raise RuntimeError("unreachable provider retry state")

    async def analyze(
        self, *, request: str, stable_candidate: str, agent: str, trace_id: str, max_tokens: int
    ) -> ProviderAgentResult:
        prompt = (
            f"Trace: {trace_id}\nAgent: {agent}\nRequest:\n{request[:6000]}\n\n"
            f"Stable candidate (may be empty):\n{stable_candidate[:6000]}\n\n"
            "Return JSON with agent, claims[{subject_id,polarity,statement,confidence}], "
            "assumptions, risks, alternatives, confidence, warnings. Polarity is -1, 0 or 1."
        )
        raw, usage = await self._complete(
            "Analyze independently as one bounded VOID v14 laboratory agent. JSON only.",
            prompt,
            max_tokens,
        )
        return ProviderAgentResult(AgentOutput.from_dict(_json_object(raw)), usage)

    async def synthesize(
        self, *, request: str, stable_candidate: str, outputs: tuple[AgentOutput, ...],
        trace_id: str, max_tokens: int,
    ) -> ProviderSynthesisResult:
        prompt = json.dumps(
            {
                "trace_id": trace_id,
                "request": request[:6000],
                "stable_candidate": stable_candidate[:6000],
                "agent_outputs": [item.to_dict() for item in outputs],
                "required": ["synthesis", "confidence", "warnings", "suggested_action"],
            },
            ensure_ascii=False,
        )
        raw, usage = await self._complete(
            "Synthesize a safe final VOID response without exposing internal deliberation. JSON only.",
            prompt,
            max_tokens,
        )
        value = _json_object(raw)
        synthesis = str(value.get("synthesis", "")).strip()
        if not synthesis:
            raise ValueError("empty v14 synthesis")
        return ProviderSynthesisResult(
            synthesis=synthesis,
            confidence=float(value.get("confidence", 0.0)),
            warnings=tuple(str(item) for item in value.get("warnings", ())),
            suggested_action=str(value.get("suggested_action", "send_synthesis")),
            usage=usage,
        )


class FakeLLMProvider:
    """Fully deterministic provider used by every v14 unit/integration test."""

    def __init__(
        self,
        outputs: Mapping[str, AgentOutput],
        *,
        synthesis: ProviderSynthesisResult | None = None,
        usage: BudgetUsage = BudgetUsage(prompt_tokens=10, completion_tokens=10),
        delay_seconds: float = 0.0,
        fail_agents: frozenset[str] = frozenset(),
        fail_synthesis: bool = False,
    ) -> None:
        self.outputs = dict(outputs)
        self.synthesis_result = synthesis or ProviderSynthesisResult(
            "deterministic synthesis", 0.8, suggested_action="send_synthesis", usage=usage
        )
        self.usage = usage
        self.delay_seconds = delay_seconds
        self.fail_agents = fail_agents
        self.fail_synthesis = fail_synthesis
        self.analysis_calls: list[tuple[str, str]] = []
        self.synthesis_calls = 0

    async def analyze(
        self, *, request: str, stable_candidate: str, agent: str, trace_id: str, max_tokens: int
    ) -> ProviderAgentResult:
        self.analysis_calls.append((trace_id, agent))
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if agent in self.fail_agents:
            raise RuntimeError("fake provider failure")
        if agent not in self.outputs:
            raise RuntimeError(f"no deterministic output for {agent}")
        return ProviderAgentResult(self.outputs[agent], self.usage)

    async def synthesize(
        self, *, request: str, stable_candidate: str, outputs: tuple[AgentOutput, ...],
        trace_id: str, max_tokens: int,
    ) -> ProviderSynthesisResult:
        self.synthesis_calls += 1
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        if self.fail_synthesis:
            raise RuntimeError("fake synthesis failure")
        return self.synthesis_result
