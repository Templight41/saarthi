"""Provider selection.

A missing key never breaks the app: it falls back to the deterministic mock
and the health endpoint reports which provider is actually live.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .base import FallbackProvider, LLMProvider
from .mock import MockProvider

logger = logging.getLogger(__name__)


def build_provider(settings: Settings) -> LLMProvider:
    mock = MockProvider()

    if settings.llm_provider == "mock":
        return mock

    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning("LLM_PROVIDER=gemini but GEMINI_API_KEY is empty; using mock provider")
            return mock
        from .gemini import GeminiProvider

        try:
            primary = GeminiProvider(
                settings.gemini_api_key, settings.gemini_model, settings.llm_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gemini provider could not be constructed (%s); using mock", exc)
            return mock
        return FallbackProvider(primary, mock)

    if settings.llm_provider == "sarvam":
        if not settings.sarvam_api_key:
            logger.warning("LLM_PROVIDER=sarvam but SARVAM_API_KEY is empty; using mock provider")
            return mock
        from .sarvam import SarvamProvider

        try:
            primary = SarvamProvider(
                settings.sarvam_api_key, settings.sarvam_model, settings.llm_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sarvam provider could not be constructed (%s); using mock", exc)
            return mock
        return FallbackProvider(primary, mock)

    return mock


def provider_info(provider: LLMProvider) -> dict:
    return {
        "provider": provider.name,
        "simulated": getattr(provider, "simulated", False),
    }
