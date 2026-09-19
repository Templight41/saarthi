"""Gemini provider using the google-genai SDK.

The same SDK talks to two different backends, and which one you get depends
entirely on how the client is constructed:

  * **Vertex AI** — `Client(vertexai=True, project=…, location=…)`, authenticated
    with Application Default Credentials. This is the path for a Google Cloud
    project: quota, billing, audit logging and data residency all follow the
    project rather than a personal key.
  * **Gemini Developer API** — `Client(api_key=…)`, an AI Studio key.

`GEMINI_BACKEND` selects between them. Everything downstream is identical, so
the rest of the agent neither knows nor cares which is in use.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMError, LLMProvider

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def build_genai_client(settings):
    """Construct a google-genai client for whichever backend is configured.

    Shared with the embedding layer so both cannot drift apart.
    """
    from google import genai

    if settings.gemini_backend == "vertex":
        if not settings.google_cloud_project:
            raise LLMError(
                "GEMINI_BACKEND=vertex requires GOOGLE_CLOUD_PROJECT. "
                "Authenticate with `gcloud auth application-default login`."
            )
        return genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

    if not settings.gemini_api_key:
        raise LLMError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=settings.gemini_api_key)


class GeminiProvider(LLMProvider):
    name = "gemini"
    simulated = False

    def __init__(self, settings, model: str | None = None, timeout: float | None = None) -> None:
        self._client = build_genai_client(settings)
        self._model = model or settings.gemini_model
        self._timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self.backend = settings.gemini_backend
        # Surfaced on /api/health so the dashboard can show which backend is live.
        self.name = f"gemini/{self.backend}"

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        context: dict | None = None,
        temperature: float = 0.0,
    ) -> T:
        prompt = user if context is None else f"{user}\n\nCONTEXT:\n{json.dumps(context, default=str)}"
        raw = await self._generate(system=system, user=prompt, schema=schema, temperature=temperature)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as first_error:
            # One repair attempt: hand the validation error back to the model.
            repair = (
                f"{prompt}\n\nYour previous answer failed validation with:\n{first_error}\n"
                "Return corrected JSON only."
            )
            raw = await self._generate(
                system=system, user=repair, schema=schema, temperature=temperature
            )
            try:
                return schema.model_validate_json(raw)
            except ValidationError as second_error:
                raise LLMError(f"Gemini returned invalid JSON twice: {second_error}") from second_error

    async def _generate(self, *, system: str, user: str, schema: type[T], temperature: float) -> str:
        config = {
            "response_mime_type": "application/json",
            "response_schema": schema,
            "system_instruction": system,
            "temperature": temperature,
        }
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model, contents=user, config=config
                ),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise LLMError(f"Gemini timed out after {self._timeout}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini call failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise LLMError("Gemini returned an empty response")
        return text

    async def complete_text(self, *, system: str, user: str) -> str:
        try:
            response = await asyncio.wait_for(
                self._client.aio.models.generate_content(
                    model=self._model,
                    contents=user,
                    config={"system_instruction": system},
                ),
                timeout=self._timeout,
            )
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Gemini call failed: {exc}") from exc
        return getattr(response, "text", "") or ""
