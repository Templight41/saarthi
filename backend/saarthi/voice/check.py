"""Make the speech provider actually say something. `make check-voice`.

`make check-providers` reads configuration back; this calls the real endpoint.
The difference is not academic: bulbul:v2 was deprecated under us and every
request started failing with a flat 400, which no amount of reading the config
would have revealed.

It prints what came back rather than asserting anything, and writes the audio
out so it can be listened to — the one property no test can check is whether
"₹2,500" is read as "two thousand five hundred rupees".
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
import wave
from pathlib import Path

from ..config import Settings
from .synthesizer import SynthesisError, build_synthesizer, resolve_synthesizer

SAMPLES = [
    ("en-IN", "Your refund of ₹2,500 for TXN18293 has been scheduled."),
    ("hi-IN", "TXN18293 के लिए ₹2,500 का रिफंड शेड्यूल हो गया है।"),
]


async def main() -> int:
    settings = Settings()
    provider, model = resolve_synthesizer(settings)
    print(f"provider   {provider}")
    print(f"model      {model}")
    print(f"speaker    {settings.sarvam_tts_speaker}")

    if provider == "off":
        print("\nspeech output is switched off (TTS_PROVIDER=off)")
        return 0

    try:
        engine = build_synthesizer(settings)
    except SynthesisError as exc:
        print(f"\ncould not build a voice: {exc}")
        return 1

    out = Path(tempfile.mkdtemp(prefix="saarthi-voice-"))
    failures = 0
    for language, text in SAMPLES:
        try:
            result = await engine.synthesize(text, language=language)
        except SynthesisError as exc:
            print(f"\n{language}  FAILED  {exc}")
            failures += 1
            continue

        with wave.open(io.BytesIO(result.audio), "rb") as handle:
            seconds = handle.getnframes() / handle.getframerate()
            rate = handle.getframerate()
        path = out / f"{language}.wav"
        path.write_bytes(result.audio)
        print(
            f"\n{language}  ok  {seconds:.1f}s at {rate}Hz, "
            f"{len(result.audio) // 1024}KB in {result.latency_ms}ms"
        )
        print(f"          {path}")

    if failures:
        print("\nSpeech output is not working. Fix it or set TTS_PROVIDER=off.")
    else:
        print("\nListen to the files above: the numbers and ids should be read as words.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
