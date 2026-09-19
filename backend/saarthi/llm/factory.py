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
        if not settings.allow_simulated:
            raise RuntimeError(
                "LLM_PROVIDER=mock is refused. Set a real provider, or set "
                "ALLOW_SIMULATED=true to permit deterministic stand-ins."
            )
        return mock

    if settings.llm_provider == "gemini":
        if not settings.gemini_configured:
            missing = (
                "GOOGLE_CLOUD_PROJECT" if settings.gemini_backend == "vertex" else "GEMINI_API_KEY"
            )
            logger.warning(
                "LLM_PROVIDER=gemini with backend %s but %s is empty; using mock provider",
                settings.gemini_backend,
                missing,
            )
            if not settings.allow_simulated:
                raise RuntimeError(f"Gemini is selected but unusable: {missing} is empty")
            return mock
        from .gemini import GeminiProvider

        try:
            primary = GeminiProvider(settings)
        except Exception as exc:  # noqa: BLE001
            if not settings.allow_simulated:
                raise
            logger.warning("Gemini provider could not be constructed (%s); using mock", exc)
            return mock
        # Without a permitted stand-in there is nothing to fall back to, so a
        # runtime failure surfaces as an error instead of changing behaviour.
        return FallbackProvider(primary, mock) if settings.allow_simulated else primary

    if settings.llm_provider == "sarvam":
        if not settings.sarvam_api_key:
            if not settings.allow_simulated:
                raise RuntimeError("Sarvam is selected but SARVAM_API_KEY is empty")
            logger.warning("LLM_PROVIDER=sarvam but SARVAM_API_KEY is empty; using mock provider")
            return mock
        from .sarvam import SarvamProvider

        try:
            primary = SarvamProvider(
                settings.sarvam_api_key, settings.sarvam_model, settings.llm_timeout_seconds
            )
        except Exception as exc:  # noqa: BLE001
            if not settings.allow_simulated:
                raise
            logger.warning("Sarvam provider could not be constructed (%s); using mock", exc)
            return mock
        return FallbackProvider(primary, mock) if settings.allow_simulated else primary

    if not settings.allow_simulated:
        raise RuntimeError(f"Unknown LLM provider {settings.llm_provider!r}")
    return mock


def provider_info(provider: LLMProvider) -> dict:
    return {
        "provider": provider.name,
        "simulated": getattr(provider, "simulated", False),
    }
