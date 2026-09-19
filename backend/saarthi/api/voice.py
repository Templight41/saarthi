"""Voice, in both directions.

Input: `/transcribe` returns a transcript and nothing else. The browser then
posts that text to the ordinary message endpoint, so there is exactly one agent
pipeline and it cannot tell whether a request began as speech or typing.

Output: `/messages/{id}/speech` speaks a message Saarthi has already sent. It
takes an **id and re-reads the row**, the same discipline the verifier follows,
and it is the reason there is no endpoint that synthesises arbitrary text. Such
an endpoint would let audio exist with no audit row behind it; here every byte
the merchant hears corresponds to a persisted message that the claims guard in
`agent/messaging.py` already checked against the ledger.

Nothing in `agent/`, `tools/`, `workflows/` or `verification/` imports the
synthesizer, and only the browser calls this route, so a failure here cannot
fail a case. That is structural, and `tests/test_voice.py` asserts it stays so.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models import Case, Merchant, Message
from ..runtime import SaarthiRuntime
from ..services import ops_service
from ..voice import languages
from ..voice.synthesizer import SynthesisError, SynthesisResult
from ..voice.transcriber import to_wav
from .deps import get_runtime, get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])

MAX_BYTES = 25 * 1024 * 1024
# Audio is bulky — 22 kHz 16-bit mono is ~44 KB/s — so the cache is bounded by
# bytes rather than by entry count, which would say nothing about memory.
CACHE_MAX_BYTES = 16 * 1024 * 1024


def _transcriber_for(runtime: SaarthiRuntime):
    return runtime.get_transcriber()


@router.get("/languages")
async def list_languages() -> dict:
    """Everything the transcriber understands, and which of those can be spoken.

    Two different capabilities: Sarvam understands two dozen Indian languages
    and bulbul says eleven of them, so `speakable` is not decoration.
    """
    return {"languages": languages.catalogue(), "auto": languages.AUTO}


@router.post("/transcribe")
async def transcribe(
    audio: UploadFile = File(...),
    hint: str | None = Form(None),
    language: str | None = Form(None),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> dict:
    payload = await audio.read()
    if not payload:
        raise HTTPException(400, "Empty audio upload")
    if len(payload) > MAX_BYTES:
        raise HTTPException(413, "Audio file is too large")

    suffix = Path(audio.filename or "clip.webm").suffix or ".webm"
    tmp_dir = Path(tempfile.mkdtemp())
    converted: Path | None = None
    try:
        source = tmp_dir / f"clip{suffix}"
        source.write_bytes(payload)

        transcriber = _transcriber_for(runtime)
        wav = to_wav(source)
        converted = wav if wav != source else None
        result = await transcriber.transcribe(wav, hint=hint, language=language)
        return result.as_dict()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if converted is not None:
            shutil.rmtree(converted.parent, ignore_errors=True)


# --------------------------------------------------------------------------
# Speech output
# --------------------------------------------------------------------------
class _AudioCache:
    """Keyed on the message *content*, not just its id.

    `/api/simulation/reset` resets the counters table, so MSG-1 after a reset
    is a different sentence at the same id. Hashing the body is what stops the
    merchant hearing the previous demo's message.
    """

    def __init__(self, max_bytes: int = CACHE_MAX_BYTES) -> None:
        self._entries: OrderedDict[str, SynthesisResult] = OrderedDict()
        self._bytes = 0
        self._max_bytes = max_bytes

    def get(self, key: str) -> SynthesisResult | None:
        entry = self._entries.get(key)
        if entry is not None:
            self._entries.move_to_end(key)
        return entry

    def put(self, key: str, result: SynthesisResult) -> None:
        if len(result.audio) > self._max_bytes:
            return
        self._entries[key] = result
        self._bytes += len(result.audio)
        while self._bytes > self._max_bytes and self._entries:
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= len(evicted.audio)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0


_audio_cache = _AudioCache()


def _cache_key(message: Message, synthesizer, language: str) -> str:
    digest = hashlib.sha256(message.content.encode("utf-8")).hexdigest()[:16]
    return ":".join(
        [
            message.id,
            digest,
            getattr(synthesizer, "name", "?"),
            str(getattr(synthesizer, "model", "?")),
            language,
        ]
    )


async def _speakable_message(session: AsyncSession, message_id: str) -> Message:
    """The one place that decides what Saarthi is allowed to say out loud.

    Every refusal is a 404: these are resources that are not speakable, not
    malformed requests, and a distinct status would confirm the existence of a
    row the client is being told it cannot see.
    """
    message = await session.get(Message, message_id)
    if message is None:
        raise HTTPException(404, "No such message")

    if not ops_service.is_merchant_visible(message):
        # Reachable in normal operation: a plan can die between draft_message
        # and send_message, which is what recovery/manager.py exists to catch.
        logger.info("Refusing to speak %s: the merchant has not seen it", message_id)
        raise HTTPException(404, "No such message")

    if (message.meta or {}).get("claims") is None:
        # The claims guard stamps every message it approves. No stamp, no voice.
        logger.info("Refusing to speak %s: no claims-guard record", message_id)
        raise HTTPException(404, "No such message")

    return message


async def _language_for(session: AsyncSession, message: Message, runtime: SaarthiRuntime) -> str:
    """Read the language off the message, not off the merchant.

    They usually agree, but when drafting falls back to a template the body is
    English whatever the merchant speaks. Taking the language from the message
    keeps the audio and the text on screen the same language, always.
    """
    stamped = (message.meta or {}).get("language")
    if stamped:
        return stamped

    if message.case_id:
        owner = await session.get(Case, message.case_id)
        if owner is not None:
            merchant = await session.get(Merchant, owner.merchant_id)
            if merchant is not None and merchant.language:
                return merchant.language
    return runtime.settings.speech_language


@router.get("/messages/{message_id}/speech")
async def speak_message(
    message_id: str,
    session: AsyncSession = Depends(get_session),
    runtime: SaarthiRuntime = Depends(get_runtime),
) -> Response:
    if runtime.settings.tts_provider == "off":
        raise HTTPException(503, "Speech output is switched off")

    message = await _speakable_message(session, message_id)
    text = (message.content or "").strip()
    if not text:
        raise HTTPException(404, "No such message")
    if len(text) > runtime.settings.tts_max_characters:
        # Never truncated: cutting "the refund has not completed yet" in half
        # would invert what Saarthi said.
        raise HTTPException(422, "This message is too long to speak")

    try:
        synthesizer = runtime.get_synthesizer()
    except SynthesisError as exc:
        raise HTTPException(503, str(exc)) from exc
    if synthesizer is None:
        raise HTTPException(503, "Speech output is switched off")

    language = await _language_for(session, message, runtime)
    if not languages.is_speakable(language):
        # Reading Assamese with an English reader is worse than staying quiet.
        # The merchant still has the text; the UI hides the speaker button.
        raise HTTPException(
            415, f"{languages.name_of(language)} can be written but not spoken"
        )
    key = _cache_key(message, synthesizer, language)

    cached = _audio_cache.get(key)
    if cached is not None:
        return _audio_response(cached, key, cache="hit")

    try:
        result = await synthesizer.synthesize(text, language=language)
    except SynthesisError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.warning("Synthesis failed for %s: %s", message_id, exc)
        raise HTTPException(502, "The speech provider failed") from exc

    _audio_cache.put(key, result)
    return _audio_response(result, key, cache="miss")


def _audio_response(result: SynthesisResult, key: str, *, cache: str) -> Response:
    return Response(
        content=result.audio,
        media_type=result.mime_type,
        headers={
            "ETag": f'"{key}"',
            # A sent message never changes, so its audio never does either.
            "Cache-Control": "private, max-age=3600, immutable",
            "X-Saarthi-Voice-Provider": result.provider,
            "X-Saarthi-Voice-Model": result.model,
            "X-Saarthi-Voice-Language": result.language,
            "X-Saarthi-Voice-Latency-Ms": str(result.latency_ms),
            "X-Saarthi-Voice-Simulated": "true" if result.simulated else "false",
            "X-Saarthi-Voice-Cache": cache,
        },
    )
