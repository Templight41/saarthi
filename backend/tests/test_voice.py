"""Voice, in both directions.

The tests that matter most here are not the happy paths. They are the ones that
pin down what Saarthi is *not* allowed to say: nothing the merchant has not
already been sent, nothing the claims guard has not stamped, and nothing
truncated. Plus one structural test proving the agent never touches the
synthesizer at all, so a dead speech provider cannot fail a case.
"""

from __future__ import annotations

import base64
import io
import json
import pathlib
import wave

import httpx
import pytest

from saarthi.config import Settings
from saarthi.database.enums import MessageStatus
from saarthi.runtime import SaarthiRuntime
from saarthi.services import ops_service
from saarthi.voice import synthesizer as synth
from saarthi.voice.synthesizer import (
    MockSynthesizer,
    SarvamSynthesizer,
    SynthesisError,
    build_synthesizer,
    decode_audios,
    normalise_language,
    resolve_synthesizer,
    wav_from_pcm,
)
from saarthi.voice.transcriber import resolve_transcriber

pytestmark = pytest.mark.asyncio

SPOKEN = "Your refund of ₹2,500 for TXN18293 has been scheduled."


def _wav(frames: int, sample_rate: int = 22050) -> bytes:
    return wav_from_pcm(b"\x00\x00" * frames, sample_rate=sample_rate)


def _frame_count(audio: bytes) -> int:
    with wave.open(io.BytesIO(audio), "rb") as handle:
        return handle.getnframes()


def _mock_sarvam(monkeypatch, handler) -> list[httpx.Request]:
    """Route the synthesizer's own AsyncClient through a MockTransport."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(recording)
    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=transport, **kwargs)

    monkeypatch.setattr(synth.httpx, "AsyncClient", factory)
    return seen


def _settings(**overrides) -> Settings:
    base = {"_env_file": None, "allow_simulated": True, "tts_provider": "mock"}
    return Settings(**{**base, **overrides})


# --------------------------------------------------------------------------
# The provider
# --------------------------------------------------------------------------
async def test_sarvam_is_sent_the_message_verbatim(monkeypatch):
    seen = _mock_sarvam(
        monkeypatch,
        lambda r: httpx.Response(200, json={"request_id": "r1", "audios": [
            base64.b64encode(_wav(100)).decode()
        ]}),
    )
    engine = SarvamSynthesizer("key-123", "bulbul:v3", "shubh", sample_rate=22050, temperature=0.3)
    result = await engine.synthesize(SPOKEN, language="hi-IN")

    body = json.loads(seen[0].content)
    # The whole guarantee rests on this: what is spoken is what was audited.
    assert body["text"] == SPOKEN
    assert body["language_code"] == "hi-IN"
    assert body["model"] == "bulbul:v3"
    assert body["speaker"] == "shubh"
    assert body["temperature"] == 0.3
    assert seen[0].headers["api-subscription-key"] == "key-123"

    assert result.audio[:4] == b"RIFF"
    assert result.simulated is False
    assert result.characters == len(SPOKEN)
    assert engine.characters_synthesised == len(SPOKEN)


async def test_multi_chunk_audio_keeps_every_frame(monkeypatch):
    chunks = [base64.b64encode(_wav(100)).decode(), base64.b64encode(_wav(250)).decode()]
    _mock_sarvam(
        monkeypatch, lambda r: httpx.Response(200, json={"request_id": "r", "audios": chunks})
    )
    engine = SarvamSynthesizer("k", "bulbul:v3", "shubh")
    result = await engine.synthesize(SPOKEN, language="en-IN")

    # Taking audios[0] would cut Saarthi off mid-sentence, which is a claims
    # problem rather than a cosmetic one.
    assert _frame_count(result.audio) == 350


def test_decode_audios_handles_a_split_single_file():
    whole = _wav(200)
    encoded = base64.b64encode(whole).decode()
    half = len(encoded) // 2
    # Chunks that are pieces of one base64 string, as Sarvam's own example shows.
    assert decode_audios([encoded[:half], encoded[half:]]) == whole


async def test_a_server_failure_leaks_nothing(monkeypatch):
    _mock_sarvam(
        monkeypatch,
        lambda r: httpx.Response(500, text="/Users/ari/secret/key.pem api-key=sk-live-abc"),
    )
    engine = SarvamSynthesizer("sk-live-abc", "bulbul:v3", "shubh")
    with pytest.raises(SynthesisError) as caught:
        await engine.synthesize(SPOKEN, language="en-IN")

    detail = str(caught.value)
    assert "500" in detail and "sarvam" in detail
    assert "/" not in detail and "sk-live-abc" not in detail


async def test_a_misconfiguration_says_what_is_wrong(monkeypatch):
    """A deprecated model or an incompatible speaker must not be a mystery."""
    _mock_sarvam(
        monkeypatch,
        lambda r: httpx.Response(
            400,
            json={"error": {"message": "Speaker 'anushka' is not compatible with bulbul:v3"}},
        ),
    )
    engine = SarvamSynthesizer("sk-live-abc", "bulbul:v3", "anushka")
    with pytest.raises(SynthesisError) as caught:
        await engine.synthesize(SPOKEN, language="en-IN")

    assert "not compatible" in str(caught.value)


async def test_the_mock_voice_is_refused_unless_simulation_is_allowed():
    with pytest.raises(SynthesisError) as caught:
        build_synthesizer(_settings(allow_simulated=False))
    assert "ALLOW_SIMULATED" in str(caught.value)

    assert isinstance(build_synthesizer(_settings()), MockSynthesizer)


def test_switching_speech_off_yields_no_synthesizer():
    assert build_synthesizer(_settings(tts_provider="off")) is None


def test_sarvam_without_a_key_is_refused():
    with pytest.raises(SynthesisError):
        build_synthesizer(_settings(tts_provider="sarvam", sarvam_api_key=""))


def test_an_unsupported_language_falls_back_rather_than_failing():
    assert normalise_language("fr-FR") == "en-IN"
    assert normalise_language(None) == "en-IN"
    assert normalise_language("ta-IN") == "ta-IN"


def test_the_resolver_agrees_with_what_is_actually_built():
    for provider in ("mock", "sarvam", "off"):
        settings = _settings(tts_provider=provider, sarvam_api_key="k")
        name, _model = resolve_synthesizer(settings)
        built = build_synthesizer(settings)
        assert name == (getattr(built, "name", None) or "off")

    settings = _settings(voice_provider="mock")
    assert resolve_transcriber(settings)[0] == "mock"


def test_missing_keys_are_refused_at_startup():
    problems = Settings(
        _env_file=None, tts_provider="sarvam", sarvam_api_key=""
    ).validate_providers()
    assert any("TTS_PROVIDER=sarvam" in p for p in problems)
    assert any("TTS_PROVIDER=off" in p for p in problems)

    assert not any(
        "TTS_PROVIDER" in p
        for p in Settings(_env_file=None, tts_provider="off").validate_providers()
    )


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------
async def test_health_reports_speech_honestly(settings, session_factory):
    runtime = SaarthiRuntime.build(settings, session_factory=session_factory)
    info = runtime.health()

    # The STT model used to be reported as the Gemini one whatever was running.
    assert info["voice"] == {"provider": "mock", "model": "scripted"}
    assert info["tts"]["provider"] == "mock"
    assert info["tts"]["enabled"] is True
    assert info["simulated"]["tts"] is True


async def test_speech_switched_off_is_not_counted_as_simulated(settings, session_factory):
    settings.tts_provider = "off"
    runtime = SaarthiRuntime.build(settings, session_factory=session_factory)
    info = runtime.health()

    # An absent feature is not a stand-in standing in for a real one.
    assert info["tts"]["enabled"] is False
    assert info["simulated"]["tts"] is False


# --------------------------------------------------------------------------
# Structural isolation
# --------------------------------------------------------------------------
def test_the_agent_never_reaches_for_the_synthesizer():
    """TTS cannot fail a case because the agent cannot call it at all."""
    root = pathlib.Path(__file__).resolve().parents[1] / "saarthi"
    offenders = [
        path.relative_to(root)
        for package in ("agent", "workflows", "tools", "verification", "recovery", "escalation", "policy")
        for path in (root / package).rglob("*.py")
        if "synthesizer" in path.read_text()
    ]
    assert offenders == []


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------
async def _resolved_case(client) -> tuple[str, list[dict]]:
    created = await client.post(
        "/api/cases",
        json={
            "message": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
            "transaction_id": "TXN_NORMAL_SUCCESS",
        },
    )
    case_id = created.json()["id"]
    messages = (await client.get(f"/api/cases/{case_id}/messages")).json()["messages"]
    return case_id, messages


def _outbound(messages: list[dict]) -> dict:
    return next(m for m in messages if m["direction"] == "OUTBOUND")


async def test_saarthi_speaks_a_message_it_has_sent(client):
    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)

    response = await client.get(f"/api/voice/messages/{message['id']}/speech")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"
    assert response.headers["x-saarthi-voice-provider"] == "mock"
    assert response.headers["x-saarthi-voice-cache"] == "miss"
    assert response.headers["etag"]


async def test_repeat_requests_are_served_from_the_cache(client):
    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)
    path = f"/api/voice/messages/{message['id']}/speech"

    first = await client.get(path)
    second = await client.get(path)

    assert first.headers["x-saarthi-voice-cache"] == "miss"
    assert second.headers["x-saarthi-voice-cache"] == "hit"
    assert first.content == second.content


async def test_the_cache_is_keyed_on_what_the_message_says(client):
    """`/api/simulation/reset` restarts the counters, so the same id later
    carries a different sentence. Keying on the id alone would have the
    merchant hear the previous demo's message."""
    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)
    path = f"/api/voice/messages/{message['id']}/speech"

    before = await client.get(path)

    async with client.runtime.session_factory() as session:
        row = await ops_service.get_message(session, message["id"])
        row.content = "A completely different sentence, spoken later on."
        await session.commit()

    after = await client.get(path)
    assert after.headers["x-saarthi-voice-cache"] == "miss"
    assert after.content != before.content


async def test_an_unknown_message_is_not_speakable(client):
    assert (await client.get("/api/voice/messages/MSG-9999/speech")).status_code == 404


async def test_saarthi_will_not_speak_the_merchants_own_words(client):
    _case_id, messages = await _resolved_case(client)
    inbound = next(m for m in messages if m["direction"] == "INBOUND")

    assert (await client.get(f"/api/voice/messages/{inbound['id']}/speech")).status_code == 404


async def test_an_unsent_draft_is_not_speakable(client):
    """A draft may be a claims-guard rejection waiting to be redrafted."""
    case_id, _ = await _resolved_case(client)
    async with client.runtime.session_factory() as session:
        draft = await ops_service.draft_message(
            session,
            case_id=case_id,
            content="Your refund has completed.",
            meta={"stage": "REFUNDED", "claims": ["REFUND_COMPLETED"]},
        )
        await session.commit()
        draft_id = draft.id

    assert (await client.get(f"/api/voice/messages/{draft_id}/speech")).status_code == 404


async def test_a_message_the_claims_guard_never_saw_is_not_speakable(client):
    case_id, _ = await _resolved_case(client)
    async with client.runtime.session_factory() as session:
        smuggled = await ops_service.draft_message(
            session, case_id=case_id, content="Your refund has completed.", meta=None
        )
        await ops_service.send_message(session, smuggled.id)
        await session.commit()
        smuggled_id = smuggled.id

    assert (await client.get(f"/api/voice/messages/{smuggled_id}/speech")).status_code == 404


async def test_an_over_long_message_is_refused_rather_than_truncated(client):
    case_id, _ = await _resolved_case(client)
    async with client.runtime.session_factory() as session:
        long = await ops_service.draft_message(
            session,
            case_id=case_id,
            content="A very long sentence. " * 40,
            meta={"stage": "OUTCOME", "claims": []},
        )
        await ops_service.send_message(session, long.id)
        await session.commit()
        long_id = long.id

    response = await client.get(f"/api/voice/messages/{long_id}/speech")
    assert response.status_code == 422


async def test_speech_switched_off_is_a_503(client):
    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)
    client.runtime.settings.tts_provider = "off"

    assert (await client.get(f"/api/voice/messages/{message['id']}/speech")).status_code == 503


async def test_a_failing_provider_is_a_clean_502(client):
    class Boom:
        name = "sarvam"
        model = "bulbul:v3"
        characters_synthesised = 0

        async def synthesize(self, text, *, language):
            raise SynthesisError("sarvam returned 503")

    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)
    client.runtime.synthesizer = Boom()

    response = await client.get(f"/api/voice/messages/{message['id']}/speech")
    assert response.status_code == 502
    assert "/" not in response.json()["detail"]


async def test_a_dead_voice_does_not_stop_a_case_resolving(client):
    class Boom:
        name = "sarvam"
        model = "bulbul:v3"
        characters_synthesised = 0

        async def synthesize(self, text, *, language):
            raise SynthesisError("sarvam is unreachable")

    client.runtime.synthesizer = Boom()
    case_id, messages = await _resolved_case(client)

    kase = (await client.get(f"/api/cases/{case_id}")).json()
    assert kase["status"] == "RESOLVED"
    assert _outbound(messages)["content"]


# --------------------------------------------------------------------------
# Language
# --------------------------------------------------------------------------
async def test_a_template_reply_is_spoken_in_the_language_it_was_written_in(client):
    """Kaveri is served in Hindi, but the template engine only writes English."""
    async with client.runtime.session_factory() as session:
        from saarthi.database.models import Merchant

        merchant = await session.get(Merchant, "M1002")
        assert merchant.language == "hi-IN"

    created = await client.post(
        "/api/cases",
        json={
            "merchant_id": "M1002",
            "message": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
            "transaction_id": "TXN_NORMAL_SUCCESS",
        },
    )
    case_id = created.json()["id"]
    async with client.runtime.session_factory() as session:
        rows = await ops_service.list_messages(session, case_id)
        outbound = [m for m in rows if m.status == MessageStatus.SENT and m.meta]

    # Claiming hi-IN here would have bulbul read English words in a Hindi voice.
    assert outbound and all(m.meta["language"] == "en-IN" for m in outbound)


async def test_the_language_stamped_on_the_message_is_what_gets_spoken(client, monkeypatch):
    _case_id, messages = await _resolved_case(client)
    message = _outbound(messages)

    async with client.runtime.session_factory() as session:
        row = await ops_service.get_message(session, message["id"])
        row.meta = {**row.meta, "language": "ta-IN"}
        await session.commit()

    spoken: list[str] = []

    class Recorder(MockSynthesizer):
        async def synthesize(self, text, *, language):
            spoken.append(language)
            return await super().synthesize(text, language=language)

    client.runtime.synthesizer = Recorder()
    await client.get(f"/api/voice/messages/{message['id']}/speech")
    assert spoken == ["ta-IN"]


# --------------------------------------------------------------------------
# One pipeline
# --------------------------------------------------------------------------
async def test_voice_and_text_reach_the_same_terminal_state(client):
    text = "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?"

    async def open_case(channel: str) -> dict:
        response = await client.post(
            "/api/cases",
            json={"message": text, "transaction_id": "TXN_NORMAL_SUCCESS", "channel": channel},
        )
        return response.json()

    typed = await open_case("CHAT")
    spoken = await open_case("VOICE")

    assert (typed["intent"], typed["status"], typed["resolution"]) == (
        spoken["intent"],
        spoken["status"],
        spoken["resolution"],
    )

    async def decision_path(case_id: str) -> list[str]:
        body = (await client.get(f"/api/cases/{case_id}/timeline")).json()
        # Memory and workflow events are excluded: the second of two identical
        # cases legitimately stores different things. What must match is the
        # reasoning — perceive, diagnose, check policy, plan, act, verify.
        return [
            e["type"]
            for e in body["events"]
            if not e["type"].startswith(("MEMORY_", "WORKFLOW_"))
        ]

    assert await decision_path(typed["id"]) == await decision_path(spoken["id"])

    # How it arrived is the only thing the system is allowed to notice.
    assert typed["origin"] == "MERCHANT_CHAT"
    assert spoken["origin"] == "MERCHANT_VOICE"


async def test_the_channel_is_recorded_on_the_inbound_event(client):
    created = await client.post(
        "/api/cases",
        json={
            "message": "Can you confirm whether TXN_NORMAL_SUCCESS went through fine?",
            "transaction_id": "TXN_NORMAL_SUCCESS",
            "channel": "VOICE",
        },
    )
    events = (await client.get(f"/api/cases/{created.json()['id']}/timeline")).json()["events"]
    received = next(e for e in events if e["type"] == "MESSAGE_RECEIVED")
    assert received["metadata"]["channel"] == "VOICE"


# --------------------------------------------------------------------------
# Transcription guards
# --------------------------------------------------------------------------
async def test_an_empty_upload_is_refused(client):
    response = await client.post(
        "/api/voice/transcribe", files={"audio": ("clip.webm", b"", "audio/webm")}
    )
    assert response.status_code == 400


async def test_an_oversized_upload_is_refused(client):
    payload = b"0" * (25 * 1024 * 1024 + 1)
    response = await client.post(
        "/api/voice/transcribe", files={"audio": ("clip.webm", payload, "audio/webm")}
    )
    assert response.status_code == 413
