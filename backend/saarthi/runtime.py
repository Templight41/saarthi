"""Composition root.

Everything is constructed once and hung off the app, so a test can swap any
single collaborator without touching the rest.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .agent.diagnosis import Diagnoser
from .agent.planner import Planner
from .agent.runner import CaseRunner
from .agent.supervisor import Supervisor
from .config import Settings
from .database.database import make_engine, make_session_factory
from .escalation.manager import EscalationManager
from .llm.factory import build_provider
from .memory.service import build_memory
from .policy.engine import PolicyEngine
from .recovery.manager import RecoveryManager
from .simulation.failure_injection import simulation_state
from .tools.catalog import registry
from .tools.executor import ActionExecutor
from .verification.verifier import Verifier


@dataclass
class SaarthiRuntime:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    llm: object
    memory: object
    registry: object
    executor: ActionExecutor
    policy: PolicyEngine
    planner: Planner
    diagnoser: Diagnoser
    verifier: Verifier
    recovery: RecoveryManager
    escalation: EscalationManager
    supervisor: Supervisor
    runner: CaseRunner
    workflows: object | None = None
    # Speech engines are built on first use, never in build(): loading a local
    # model is expensive and speech is optional, so it must not be able to slow
    # or break startup. Declared fields rather than stashed attributes so a
    # test can inject a failing one.
    transcriber: object | None = None
    synthesizer: object | None = None

    def get_transcriber(self):
        if self.transcriber is None:
            from .voice.transcriber import build_transcriber

            self.transcriber = build_transcriber(self.settings)
        return self.transcriber

    def get_synthesizer(self):
        """None when speech output is switched off — an absence, not a failure."""
        if self.synthesizer is None and self.settings.tts_provider != "off":
            from .voice.synthesizer import build_synthesizer

            self.synthesizer = build_synthesizer(self.settings)
        return self.synthesizer

    @classmethod
    def build(
        cls,
        settings: Settings,
        *,
        engine: AsyncEngine | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> SaarthiRuntime:
        engine = engine or make_engine(settings)
        session_factory = session_factory or make_session_factory(engine)

        llm = build_provider(settings)
        memory = build_memory(settings)
        bind = getattr(memory, "bind", None)
        if bind is not None:
            bind(session_factory)
        policy = PolicyEngine()
        planner = Planner(settings)
        executor = ActionExecutor(registry)
        diagnoser = Diagnoser(llm, registry=registry)
        verifier = Verifier()
        recovery = RecoveryManager(settings, policy)
        escalation = EscalationManager()

        from .workflows.engine import build_workflow_engine

        workflows = build_workflow_engine(settings)

        supervisor = Supervisor(
            session_factory,
            settings=settings,
            llm=llm,
            memory=memory,
            registry=registry,
            executor=executor,
            policy=policy,
            planner=planner,
            diagnoser=diagnoser,
            verifier=verifier,
            recovery=recovery,
            escalation=escalation,
            simulation=simulation_state,
            workflows=workflows,
        )
        runner = CaseRunner(supervisor, settings)
        if workflows is not None:
            workflows.bind(runner, session_factory, settings)

        return cls(
            settings=settings,
            engine=engine,
            session_factory=session_factory,
            llm=llm,
            memory=memory,
            registry=registry,
            executor=executor,
            policy=policy,
            planner=planner,
            diagnoser=diagnoser,
            verifier=verifier,
            recovery=recovery,
            escalation=escalation,
            supervisor=supervisor,
            runner=runner,
            workflows=workflows,
        )

    def health(self) -> dict:
        from .llm.factory import provider_info
        from .voice.synthesizer import resolve_synthesizer
        from .voice.transcriber import resolve_transcriber

        llm = provider_info(self.llm)
        memory_name = getattr(self.memory, "name", "unknown")
        workflow_name = getattr(self.workflows, "name", "none")

        # Report what would actually run, not what was configured: VOICE_PROVIDER=auto
        # with nothing configured resolves to the scripted transcriber, and saying
        # otherwise would make `all_real` a lie.
        stt_provider, stt_model = resolve_transcriber(self.settings)
        stt_provider = getattr(self.transcriber, "name", None) or stt_provider
        tts_provider, tts_model = resolve_synthesizer(self.settings)

        simulated = {
            "llm": bool(llm.get("simulated")),
            "memory": memory_name == "local_index",
            "workflows": workflow_name == "local",
            "voice": stt_provider == "mock",
            # "off" is a feature switched off, not a stand-in standing in.
            "tts": tts_provider == "mock",
        }
        return {
            "status": "ok",
            "llm": {**llm, "backend": getattr(self.llm, "backend", None)},
            "memory": {"provider": memory_name},
            "workflows": {"engine": workflow_name},
            "voice": {"provider": stt_provider, "model": stt_model},
            "tts": {
                "provider": tts_provider,
                "model": tts_model,
                "speaker": self.settings.sarvam_tts_speaker,
                "language": self.settings.speech_language,
                "enabled": tts_provider != "off",
                "characters_synthesised": getattr(self.synthesizer, "characters_synthesised", 0),
            },
            "database": "postgres" if not self.settings.is_sqlite else "sqlite",
            "simulated": simulated,
            "all_real": not any(simulated.values()),
        }
