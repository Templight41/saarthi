"""Controlled tool registry.

The agent gets exactly these tools and nothing else. No arbitrary SQL, no
arbitrary HTTP. Each spec declares whether the tool has side effects, whether
it needs a policy decision, whether it is safe to retry, and which verifier
check confirms its outcome.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.events import EventType
from ..database.models import Case
from ..simulation.failure_injection import SimulationState


@dataclass
class ToolContext:
    session: AsyncSession
    case: Case
    simulation: SimulationState
    runtime: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, BaseModel], Awaitable[dict]]
    side_effecting: bool = False
    requires_policy: bool = False
    idempotent: bool = False
    verify_with: str | None = None
    event_on_success: EventType | None = None
    # Derives the idempotency key from the case and args. The executor records
    # it on the action row so recovery can ask "did this already happen?".
    # Deterministic by construction, so a retry reuses the same key.
    idempotency_key_fn: Callable[[Case, dict], str | None] | None = None


class UnknownTool(KeyError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Unknown tool: {name}")
        self.name = name


class ToolRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        self._specs[spec.name] = spec
        return spec

    def tool(self, **kwargs: Any) -> Callable:
        def decorator(fn: Callable[[ToolContext, BaseModel], Awaitable[dict]]) -> Callable:
            self.register(ToolSpec(handler=fn, **kwargs))
            return fn

        return decorator

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownTool(name) from None

    def names(self) -> set[str]:
        return set(self._specs)

    def llm_visible(self) -> list[dict]:
        """Read-only tools only, if a tool-calling interface is ever exposed."""
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_model.model_json_schema(),
            }
            for spec in self._specs.values()
            if not spec.side_effecting
        ]
