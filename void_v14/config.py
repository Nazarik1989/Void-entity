"""Single source of runtime limits for VOID v14."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class VoidV14Config:
    collapse_threshold: float = 0.75
    conflict_threshold: float = 0.35
    timeout_seconds: float = 20.0
    max_rounds: int = 0
    madness_reentry_enabled: bool = False
    concurrency: int = 3
    token_budget: int = 6000
    cost_budget_usd: float = 0.25
    input_cost_per_million_usd: float = 2.0
    output_cost_per_million_usd: float = 8.0
    transient_retries: int = 1
    retention_days: int = 30
    memory_path: Path = Path("/var/lib/void-entity/v14/experimental.sqlite3")
    agents: tuple[str, ...] = ("analyst", "skeptic", "alternative")
    allowlisted_user_ids: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if not 0.0 <= self.conflict_threshold < self.collapse_threshold <= 1.0:
            raise ValueError("thresholds must satisfy 0 <= conflict < collapse <= 1")
        if self.timeout_seconds <= 0 or self.concurrency <= 0:
            raise ValueError("timeout and concurrency must be positive")
        if self.token_budget <= 0 or self.cost_budget_usd <= 0:
            raise ValueError("budgets must be positive")
        if self.input_cost_per_million_usd < 0 or self.output_cost_per_million_usd < 0:
            raise ValueError("cost estimation rates cannot be negative")
        if self.max_rounds < 0 or self.transient_retries < 0 or self.retention_days <= 0:
            raise ValueError("rounds, retries and retention are invalid")
        if not self.madness_reentry_enabled and self.max_rounds != 0:
            raise ValueError("disabled Madness Reentry requires max_rounds=0")
        if not self.agents:
            raise ValueError("at least one experimental agent is required")

    def allows(self, user_id: int, *, admin_id: int) -> bool:
        # Compatibility keeps the old field readable, but an allowlist is not
        # authorization. The main runtime additionally requires its disabled-
        # by-default feature flag before constructing this router.
        return bool(user_id and user_id == admin_id)
