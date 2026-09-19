"""Speech synthesis.

The mirror image of `transcriber.py`, and deliberately shaped the same way.

Sarvam's bulbul is the only real provider, because this is Indian merchant
support: it speaks the eleven Indian languages a merchant is likely to want,
with an Indian voice, which no general-purpose model does as well.

What is *not* here matters as much as what is. This module takes a string and
returns audio. It does not choose the string, translate it, expand abbreviations
or tidy punctuation. The caller hands it the exact body of a merchant message
that the claims guard already approved, and that is what the merchant hears —
so the audio carries the same guarantees the text does.
"""

from __future__ import annotations

import base64
import io
import logging
import time
import wave
from dataclasses import dataclass
from typing import Protocol

import httpx

from ..config import Settings
from .languages import DEFAULT_LANGUAGE
from .languages import SPEAKABLE as SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"



class SynthesisError(RuntimeError):
    """Synthesis failed. The message carries nothing the caller cannot show."""


@dataclass
class SynthesisResult:
    audio: bytes
    mime_type: str
    sample_rate: int
    provider: str
    model: str
    voice: str
    language: str
    latency_ms: int
    simulated: bool
    characters: int

    def as_dict(self) -> dict:
        """Everything except the audio itself, for headers and logging."""
        return {
            "mime_type": self.mime_type,
            "sample_rate": self.sample_rate,
            "provider": self.provider,
            "model": self.model,
            "voice": self.voice,
            "language": self.language,
            "latency_ms": self.latency_ms,
            "simulated": self.simulated,
            "characters": self.characters,
        }


class Synthesizer(Protocol):
    name: str
    characters_synthesised: int

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult: ...


def normalise_language(language: str | None) -> str:
    """Last-resort guard. Callers check `languages.is_speakable` first, so an
    unspeakable language should never reach here — reaching it means a message
    would be read in the wrong voice, which is worth a warning."""
    if not language:
        return DEFAULT_LANGUAGE
    if language in SUPPORTED_LANGUAGES:
        return language
    logger.warning("%s is not a bulbul language; speaking %s instead", language, DEFAULT_LANGUAGE)
    return DEFAULT_LANGUAGE


def wav_from_pcm(frames: bytes, *, sample_rate: int, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(frames)
    return buffer.getvalue()


def _concatenate_wavs(chunks: list[bytes]) -> bytes:
    """Splice several WAV files into one, keeping every frame.

    bulbul splits long input and returns a chunk per part. Playing only the
    first would cut Saarthi off mid-sentence, which in this codebase is a
    claims problem rather than a cosmetic one, so nothing may be dropped.
    """
    frames: list[bytes] = []
    params = None
    for chunk in chunks:
        with wave.open(io.BytesIO(chunk), "rb") as handle:
            params = params or handle.getparams()
            frames.append(handle.readframes(handle.getnframes()))
    if params is None:
        raise SynthesisError("The speech provider returned no audio")
    return wav_from_pcm(
        b"".join(frames),
        sample_rate=params.framerate,
        channels=params.nchannels,
        width=params.sampwidth,
    )


def decode_audios(audios: list[str]) -> bytes:
    """Turn Sarvam's base64 chunk list into one playable WAV.

    Sarvam's own example joins the base64 strings and decodes once, which works
    when the chunks are halves of a single file. It is not safe to assume that:
    if each chunk turns out to carry its own RIFF header, joining first yields
    a file whose header lies about its length. So decode first, then look.
    """
    if not audios:
        raise SynthesisError("The speech provider returned no audio")

    decoded = [base64.b64decode(chunk) for chunk in audios]
    if len(decoded) == 1:
        return decoded[0]
    if all(chunk[:4] == b"RIFF" for chunk in decoded):
        return _concatenate_wavs(decoded)
    # Not self-contained files: they are pieces of one, as Sarvam documents.
    return base64.b64decode("".join(audios))


class MockSynthesizer:
    """A short silence, so the tests exercise every path but the network."""

    name = "mock"
    model = "silence"

    def __init__(self, sample_rate: int = 22050) -> None:
        self._sample_rate = sample_rate
        self.characters_synthesised = 0

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        language = normalise_language(language)
        # A tenth of a second per ten characters: long enough to be a real file,
        # short enough that nothing waits for it.
        frames = b"\x00\x00" * int(self._sample_rate * min(len(text), 400) / 100)
        self.characters_synthesised += len(text)
        return SynthesisResult(
            audio=wav_from_pcm(frames, sample_rate=self._sample_rate),
            mime_type="audio/wav",
            sample_rate=self._sample_rate,
            provider=self.name,
            model=self.model,
            voice="scripted",
            language=language,
            latency_ms=0,
            simulated=True,
            characters=len(text),
        )


class SarvamSynthesizer:
    name = "sarvam"

    def __init__(
        self,
        api_key: str,
        model: str,
        speaker: str,
        *,
        sample_rate: int = 22050,
        temperature: float = 0.3,
        pace: float = 1.0,
        timeout: float = 25.0,
    ) -> None:
        self._api_key = api_key
        self.model = model
        self._speaker = speaker
        self._sample_rate = sample_rate
        self._temperature = temperature
        self._pace = pace
        self._timeout = timeout
        self.characters_synthesised = 0

    async def synthesize(self, text: str, *, language: str) -> SynthesisResult:
        language = normalise_language(language)
        started = time.monotonic()
        payload = {
            "text": text,
            # The name the current bulbul:v3 docs use. The legacy
            # `target_language_code` is still accepted, but so is any unknown
            # field — the API ignores what it does not recognise — so
            # acceptance is no evidence either way. Follow the documentation.
            "language_code": language,
            "model": self.model,
            "speaker": self._speaker,
            "speech_sample_rate": self._sample_rate,
            "pace": self._pace,
            "temperature": self._temperature,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    SARVAM_TTS_URL,
                    json=payload,
                    headers={"api-subscription-key": self._api_key},
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPStatusError as exc:
            raise SynthesisError(_explain(exc.response)) from exc
        except Exception as exc:  # noqa: BLE001
            raise SynthesisError(f"sarvam was unreachable ({type(exc).__name__})") from exc

        audio = decode_audios(body.get("audios") or [])
        self.characters_synthesised += len(text)
        return SynthesisResult(
            audio=audio,
            mime_type="audio/wav",
            sample_rate=self._sample_rate,
            provider=self.name,
            model=self.model,
            voice=self._speaker,
            language=language,
            latency_ms=int((time.monotonic() - started) * 1000),
            simulated=False,
            characters=len(text),
        )


def _explain(response: httpx.Response) -> str:
    """A usable reason, without echoing the provider's response wholesale.

    A misconfigured speaker or a deprecated model comes back as a 400 whose
    message names the problem exactly, and losing that turns a five-second fix
    into a mid-demo mystery. Only Sarvam's own `error.message` is passed on,
    and only for client errors; a 5xx says nothing but its status.
    """
    status = response.status_code
    if status >= 500:
        return f"sarvam returned {status}"
    try:
        message = response.json()["error"]["message"]
    except Exception:  # noqa: BLE001
        return f"sarvam returned {status}"
    return f"sarvam returned {status}: {str(message)[:200]}"


def resolve_synthesizer(settings: Settings) -> tuple[str, str]:
    """The (provider, model) `build_synthesizer` would choose.

    `health()` reports from this and `build_synthesizer` switches on it, so what
    the dashboard claims and what actually speaks cannot drift apart.
    """
    if settings.tts_provider == "off":
        return "off", ""
    if settings.tts_provider == "mock":
        return "mock", MockSynthesizer.model
    return "sarvam", settings.sarvam_tts_model


def build_synthesizer(settings: Settings) -> Synthesizer | None:
    """A real voice unless deterministic stand-ins are explicitly allowed.

    Returns None when speech output is switched off, which is an absence rather
    than a failure: the merchant still gets the message, in writing.
    """
    provider, _ = resolve_synthesizer(settings)

    if provider == "off":
        return None

    if provider == "mock":
        if not settings.allow_simulated:
            raise SynthesisError(
                "TTS_PROVIDER=mock is refused. Use sarvam, set TTS_PROVIDER=off, "
                "or set ALLOW_SIMULATED=true."
            )
        return MockSynthesizer(settings.tts_sample_rate)

    if not settings.sarvam_api_key:
        raise SynthesisError("TTS_PROVIDER=sarvam but SARVAM_API_KEY is empty")
    return SarvamSynthesizer(
        settings.sarvam_api_key,
        settings.sarvam_tts_model,
        settings.sarvam_tts_speaker,
        sample_rate=settings.tts_sample_rate,
        temperature=settings.tts_temperature,
        pace=settings.tts_pace,
        timeout=settings.tts_timeout_seconds,
    )


__all__ = [
    "DEFAULT_LANGUAGE",
    "SUPPORTED_LANGUAGES",
    "MockSynthesizer",
    "SarvamSynthesizer",
    "SynthesisError",
    "SynthesisResult",
    "Synthesizer",
    "build_synthesizer",
    "decode_audios",
    "normalise_language",
    "resolve_synthesizer",
    "wav_from_pcm",
]