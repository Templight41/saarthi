"""Voice input.

This endpoint returns a transcript and nothing else. The browser then posts
that text to the ordinary message endpoint, so there is exactly one agent
pipeline and it cannot tell whether a request began as speech or typing.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..runtime import SaarthiRuntime
from ..voice.transcriber import build_transcriber, to_wav
from .deps import get_runtime

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_BYTES = 25 * 1024 * 1024


def _transcriber_for(runtime: SaarthiRuntime):
    """Built once per process: loading a local model is expensive."""
    existing = getattr(runtime, "_transcriber", None)
    if existing is None:
        existing = build_transcriber(runtime.settings)
        runtime._transcriber = existing
    return existing


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    hint: str | None = Form(None),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "Empty audio upload")
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, "Audio file is too large")

    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    tmp_dir = Path(tempfile.mkdtemp())
    source = tmp_dir / f"clip{suffix}"
    source.write_bytes(payload)

    transcriber = _transcriber_for(runtime)
    wav = to_wav(source)
    result = await transcriber.transcribe(wav, hint=hint)
    return result.as_dict()
