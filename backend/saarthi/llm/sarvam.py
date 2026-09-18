"""Sarvam chat provider.

Sarvam's chat API is OpenAI-compatible, so this speaks plain HTTP rather than
pulling the SDK in. Two live-API details matter and are easy to miss:
`max_tokens` defaults to 2048, and `strict` on json_schema defaults to false.

Model names changed in early 2026: `sarvam-105b` replaced `sarvam-m`.
"""

from __future__ import annotations

import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .base import LLMError, LLMProvider

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

SARVAM_BASE_URL = "https://api.sarvam.ai/v1"


class SarvamProvider(LLMProvider):
    name = "sarvam"
    simulated = False

    def __init__(self, api_key: str, model: str, timeout: float = 20.0) -> None:
        if not api_key:
            raise LLMError("SARVAM_API_KEY is not set")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "api-subscription-key": self._api_key,
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

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
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": 2048,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": True,
                },
            },
        }
        raw = await self._post(payload)
        try:
            return schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMError(f"Sarvam returned invalid JSON: {exc}") from exc

    async def _post(self, payload: dict) -> str:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{SARVAM_BASE_URL}/chat/completions", json=payload, headers=self._headers()
                )
                response.raise_for_status()
                data = response.json()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"Sarvam call failed: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected Sarvam response shape: {data}") from exc

    async def complete_text(self, *, system: str, user: str) -> str:
        return await self._post(
            {
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_tokens": 2048,
            }
        )
