"""Gemini provider using the google-genai SDK."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMError, LLMProvider

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class GeminiProvider(LLMProvider):
    name = "gemini"
    simulated = False

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set")
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._timeout = timeout

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
