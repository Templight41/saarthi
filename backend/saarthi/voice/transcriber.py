"""Speech to text.

Sarvam first, because this is Indian merchant support: on the one independent
benchmark available its word error rate on Indian English was 34.3 against
Whisper large-v3's 46.8, and on Hindi 39.0 against 71.7. It also has a code-mix
mode, which is how these merchants actually speak.

faster-whisper is the offline path, and a scripted mock keeps the demo alive
with no key and no model download. Whatever transcribes, the resulting text
goes through the ordinary message endpoint, so the agent cannot tell voice from
chat.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"


@dataclass
class TranscribeResult:
    text: str
    language: str | None
    duration_seconds: float
    provider: str
    model: str
    latency_ms: int
    simulated: bool

    def as_dict(self) -> dict:
        return {
            "text": self.text,
            "language": self.language,
            "duration_seconds": self.duration_seconds,
            "provider": self.provider,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "simulated": self.simulated,
        }


class Transcriber(Protocol):
    name: str

    async def transcribe(self, path: Path, *, hint: str | None = None) -> TranscribeResult: ...


class MockTranscriber:
    """Scripted, so a demo survives a dead microphone or a missing key."""

    name = "mock"

    SCRIPTS = {
        "scenario_a": "My customer's payment failed, but the money was deducted.",
        "scenario_b": (
            "The customer cancelled order A-5521 and wants the two thousand "
            "five hundred rupees refunded."
        ),
        "scenario_c": (
            "The customer says the product quality was poor and wants a "
            "fifteen thousand rupee partial refund."
        ),
        "scenario_d": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
    }

    def __init__(self) -> None:
        self._index = 0

    async def transcribe(self, path: Path, *, hint: str | None = None) -> TranscribeResult:
        if hint and hint in self.SCRIPTS:
            text = self.SCRIPTS[hint]
        else:
            keys = list(self.SCRIPTS)
            text = self.SCRIPTS[keys[self._index % len(keys)]]
            self._index += 1
        return TranscribeResult(
            text=text,
            language="en-IN",
            duration_seconds=0.0,
            provider=self.name,
            model="scripted",
            latency_ms=0,
            simulated=True,
        )


class SarvamTranscriber:
    name = "sarvam"

    def __init__(self, api_key: str, model: str, language: str) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language

    async def transcribe(self, path: Path, *, hint: str | None = None) -> TranscribeResult:
        import time

        started = time.monotonic()
        data = {
            "model": self._model,
            "language_code": self._language,
            # Hinglish is the norm, not the exception, for these merchants.
            "mode": "codemix",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            with path.open("rb") as handle:
                response = await client.post(
                    SARVAM_STT_URL,
                    headers={"api-subscription-key": self._api_key},
                    data=data,
                    files={"file": (path.name, handle, "audio/wav")},
                )
            response.raise_for_status()
            payload = response.json()

        return TranscribeResult(
            text=payload.get("transcript", ""),
            language=payload.get("language_code", self._language),
            duration_seconds=0.0,
            provider=self.name,
            model=self._model,
            latency_ms=int((time.monotonic() - started) * 1000),
            simulated=False,
        )


class FasterWhisperTranscriber:
    name = "faster_whisper"

    def __init__(self, model: str, compute_type: str) -> None:
        from faster_whisper import WhisperModel

        self._model_name = model
        self._model = WhisperModel(model, device="cpu", compute_type=compute_type)

    async def transcribe(self, path: Path, *, hint: str | None = None) -> TranscribeResult:
        import time

        started = time.monotonic()

        def _run():
            segments, info = self._model.transcribe(
                str(path), beam_size=1, vad_filter=True, language="en"
            )
            return " ".join(s.text.strip() for s in segments).strip(), info

        text, info = await asyncio.to_thread(_run)
        return TranscribeResult(
            text=text,
            language=getattr(info, "language", "en"),
            duration_seconds=getattr(info, "duration", 0.0),
            provider=self.name,
            model=self._model_name,
            latency_ms=int((time.monotonic() - started) * 1000),
            simulated=False,
        )


def build_transcriber(settings: Settings) -> Transcriber:
    provider = settings.voice_provider

    if provider == "mock" or settings.whisper_model == "mock":
        return MockTranscriber()

    if provider in {"sarvam", "auto"} and settings.sarvam_api_key:
        return SarvamTranscriber(
            settings.sarvam_api_key, settings.sarvam_stt_model, settings.voice_language
        )

    if provider in {"faster_whisper", "auto"}:
        try:
            return FasterWhisperTranscriber(settings.whisper_model, settings.whisper_compute_type)
        except ImportError:
            logger.warning("faster-whisper is not installed; using the scripted transcriber")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load faster-whisper (%s); using the scripted transcriber", exc)

    return MockTranscriber()


def to_wav(source: Path) -> Path:
    """Browsers record webm/opus; models want 16 kHz mono wav."""
    if shutil.which("ffmpeg") is None:
        return source
    target = Path(tempfile.mkdtemp()) / "audio.wav"
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", str(source), "-ac", "1", "-ar", "16000", "-f", "wav", str(target)],
        capture_output=True,
    )
    if result.returncode != 0 or not target.exists():
        logger.warning("ffmpeg conversion failed; passing the original file through")
        return source
    return target
