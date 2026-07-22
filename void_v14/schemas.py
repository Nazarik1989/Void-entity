"""Typed contracts for the isolated VOID v14 laboratory."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class ExperimentalState(str, Enum):
    COHERENT = "A"
    CONFLICT = "B"
    COLLAPSE = "C"


class ConflictLevel(str, Enum):
    COHERENT = "COHERENT"
    CONFLICT = "CONFLICT"
    COLLAPSE = "COLLAPSE"


@dataclass(frozen=True, slots=True)
class Claim:
    subject_id: str
    polarity: int
    statement: str
    confidence: float = 0.5

    def __post_init__(self) -> None:
        if not self.subject_id.strip():
            raise ValueError("claim subject_id is required")
        if self.polarity not in {-1, 0, 1}:
            raise ValueError("claim polarity must be -1, 0 or 1")
        if not self.statement.strip():
            raise ValueError("claim statement is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("claim confidence must be 0..1")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Claim":
        return cls(
            subject_id=str(value.get("subject_id", "")),
            polarity=int(value.get("polarity", 0)),
            statement=str(value.get("statement", "")),
            confidence=float(value.get("confidence", 0.5)),
        )


@dataclass(frozen=True, slots=True)
class AgentOutput:
    agent: str
    claims: tuple[Claim, ...]
    assumptions: tuple[str, ...]
    risks: tuple[str, ...]
    alternatives: tuple[str, ...]
    confidence: float
    warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.agent.strip():
            raise ValueError("agent is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("agent confidence must be 0..1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AgentOutput":
        claims = value.get("claims", ())
        if not isinstance(claims, (list, tuple)):
            raise ValueError("claims must be a list")
        return cls(
            agent=str(value.get("agent", "")),
            claims=tuple(Claim.from_dict(item) for item in claims if isinstance(item, Mapping)),
            assumptions=tuple(str(item) for item in value.get("assumptions", ())),
            risks=tuple(str(item) for item in value.get("risks", ())),
            alternatives=tuple(str(item) for item in value.get("alternatives", ())),
            confidence=float(value.get("confidence", 0.0)),
            warnings=tuple(str(item) for item in value.get("warnings", ())),
        )


@dataclass(frozen=True, slots=True)
class BudgetUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def plus(self, other: "BudgetUsage") -> "BudgetUsage":
        return BudgetUsage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            round(self.estimated_cost_usd + other.estimated_cost_usd, 8),
        )


@dataclass(frozen=True, slots=True)
class ExperimentalResult:
    trace_id: str
    state: ExperimentalState
    agent_outputs: tuple[AgentOutput, ...]
    conflict_score: float
    conflict_level: ConflictLevel
    synthesis: str
    confidence: float
    warnings: tuple[str, ...]
    suggested_action: str
    budget_usage: BudgetUsage
    rounds_used: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ProviderAgentResult:
    output: AgentOutput
    usage: BudgetUsage = BudgetUsage()


@dataclass(frozen=True, slots=True)
class ProviderSynthesisResult:
    synthesis: str
    confidence: float
    warnings: tuple[str, ...] = ()
    suggested_action: str = "send_synthesis"
    usage: BudgetUsage = BudgetUsage()
