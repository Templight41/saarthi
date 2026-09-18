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

        return {
            "status": "ok",
            "llm": provider_info(self.llm),
            "memory": {"provider": getattr(self.memory, "name", "unknown")},
            "workflows": {"engine": getattr(self.workflows, "name", "none")},
            "database": "postgres" if not self.settings.is_sqlite else "sqlite",
        }
