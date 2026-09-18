"""LLM provider abstraction.

The architecture must not depend on a single model vendor, and the demo must
run with no key at all. Every real provider is wrapped so that any failure
degrades to the deterministic mock rather than breaking the case.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    name: str = "base"
    simulated: bool = False

    @abstractmethod
    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        context: dict | None = None,
        temperature: float = 0.0,
    ) -> T: ...

    @abstractmethod
    async def complete_text(self, *, system: str, user: str) -> str: ...


class FallbackProvider(LLMProvider):
    """Try the real provider; degrade to the mock on any failure.

    `last_fallback_reason` is surfaced as an LLM_FALLBACK audit event so a
    degradation is visible on the timeline rather than silent.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}+fallback"
        self.simulated = False
        self.last_fallback_reason: str | None = None

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        context: dict | None = None,
        temperature: float = 0.0,
    ) -> T:
        self.last_fallback_reason = None
        try:
            return await self.primary.complete_json(
                system=system, user=user, schema=schema, context=context, temperature=temperature
            )
        except (LLMError, ValidationError, Exception) as exc:  # noqa: BLE001 - deliberate catch-all
            self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("LLM primary failed, falling back to mock: %s", self.last_fallback_reason)
            return await self.fallback.complete_json(
                system=system, user=user, schema=schema, context=context, temperature=temperature
            )

    async def complete_text(self, *, system: str, user: str) -> str:
        self.last_fallback_reason = None
        try:
            return await self.primary.complete_text(system=system, user=user)
        except Exception as exc:  # noqa: BLE001
            self.last_fallback_reason = f"{type(exc).__name__}: {exc}"
            logger.warning("LLM primary failed, falling back to mock: %s", self.last_fallback_reason)
            return await self.fallback.complete_text(system=system, user=user)
