"""Manual, allowlisted routing for the VOID v14 laboratory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .config import VoidV14Config
from .engine import VoidV14Engine
from .schemas import ExperimentalResult


StableGenerate = Callable[[str, bool], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class RouteOutcome:
    mode: str
    response: str
    allowed: bool
    persist_stable_after_send: bool = False
    experimental_result: ExperimentalResult | None = None


class VoidV14Router:
    def __init__(
        self,
        *,
        config: VoidV14Config,
        admin_id: int,
        engine: VoidV14Engine | None,
    ) -> None:
        self.config = config
        self.admin_id = int(admin_id)
        self.engine = engine

    async def route(
        self,
        *,
        mode: str,
        user_id: int,
        request: str,
        stable_generate: StableGenerate,
    ) -> RouteOutcome:
        normalized = str(mode or "").strip().casefold()
        if normalized not in {"stable", "experimental", "hybrid"}:
            return RouteOutcome(normalized, "Unknown VOID v14 mode.", False)
        if not self.config.allows(int(user_id), admin_id=self.admin_id):
            return RouteOutcome(normalized, "VOID v14 is not available for this user.", False)
        if not request.strip():
            return RouteOutcome(normalized, "VOID v14 request is empty.", False)

        if normalized == "stable":
            # Do not instantiate or call v14 for stable mode.
            return RouteOutcome("stable", await stable_generate(request, True), True)
        if self.engine is None:
            return RouteOutcome(normalized, "VOID v14 laboratory provider is unavailable.", True)
        if normalized == "experimental":
            result = await self.engine.run(user_id=user_id, request=request)
            return RouteOutcome("experimental", result.synthesis, True, experimental_result=result)

        stable_candidate = await stable_generate(request, False)
        result = await self.engine.run(
            user_id=user_id,
            request=request,
            stable_candidate=stable_candidate,
        )
        return RouteOutcome(
            "hybrid",
            result.synthesis,
            True,
            persist_stable_after_send=True,
            experimental_result=result,
        )
