"""Application settings. Every external dependency has an env-selected fallback."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./saarthi.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- Core ---
    app_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://saarthi:saarthi@localhost:5432/saarthi"
    cors_origins: str = "http://localhost:5173"
    internal_api_token: str = "dev-internal-token"
    seed_on_startup: bool = True

    # --- LLM ---
    # Real by default. `allow_simulated` must be set before any deterministic
    # stand-in is permitted, so a demo cannot silently run on fixtures.
    allow_simulated: bool = False
    llm_provider: Literal["gemini", "sarvam", "mock"] = "gemini"
    # "vertex" authenticates with Application Default Credentials against a
    # Google Cloud project; "developer" uses an AI Studio API key.
    gemini_backend: Literal["vertex", "developer"] = "vertex"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    google_cloud_project: str = ""
    google_cloud_location: str = "global"
    sarvam_api_key: str = ""
    sarvam_model: str = "sarvam-105b"
    llm_timeout_seconds: float = 20.0
    diagnosis_confidence_threshold: float = 0.6

    # --- Memory ---
    memory_provider: Literal["auto", "pgvector", "local"] = "pgvector"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    memory_top_k: int = 5
    memory_timeout_seconds: float = 12.0

    # --- Workflows ---
    workflow_engine: Literal["auto", "n8n", "local"] = "n8n"
    n8n_base_url: str = "http://localhost:5678"
    n8n_webhook_url: str = "http://localhost:5678/webhook"
    scheduled_refund_wait_seconds: int = 20
    scheduled_refund_threshold_seconds: int = 60
    scheduled_refund_max_checks: int = 3
    settlement_monitor_interval_seconds: int = 30
    settlement_delay_grace_seconds: int = 900
    alert_ttl_minutes: int = 30

    # --- Voice ---
    voice_provider: Literal["auto", "gemini", "sarvam", "faster_whisper", "mock"] = "gemini"
    gemini_transcribe_model: str = "gemini-3.5-transcribe-preview"
    sarvam_stt_model: str = "saaras:v3"
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    voice_language: str = "en-IN"
    voice_max_seconds: int = 30

    # --- Agent behaviour ---
    agent_run_mode: Literal["background", "inline"] = "background"
    agent_step_delay_seconds: float = 0.4
    agent_max_steps: int = 60
    recovery_max_attempts: int = 3
    refund_grace_minutes: int = 30

    # --- Simulation ---
    fail_first_refund: bool = False

    # --- Metrics ---
    metrics_timezone: str = "Asia/Kolkata"
    human_hours_saved_per_case: float = 0.75
    human_hours_saved_per_approved_case: float = 0.25

    def validate_providers(self) -> list[str]:
        """Reject a configuration that would quietly run on stand-ins.

        Returns the problems found so the caller can fail loudly at startup
        rather than halfway through a demo.
        """
        if self.allow_simulated:
            return []

        problems: list[str] = []
        if self.llm_provider == "mock":
            problems.append("LLM_PROVIDER=mock")
        if self.llm_provider == "gemini" and not self.gemini_configured:
            missing = (
                "GOOGLE_CLOUD_PROJECT" if self.gemini_backend == "vertex" else "GEMINI_API_KEY"
            )
            problems.append(f"LLM_PROVIDER=gemini but {missing} is empty")
        if self.llm_provider == "sarvam" and not self.sarvam_api_key:
            problems.append("LLM_PROVIDER=sarvam but SARVAM_API_KEY is empty")
        if self.memory_provider == "local":
            problems.append("MEMORY_PROVIDER=local (keyword index, not embeddings)")
        if self.memory_provider in {"auto", "pgvector"} and not self.gemini_configured:
            problems.append("MEMORY_PROVIDER needs embeddings, but Gemini is not configured")
        if self.memory_provider in {"auto", "pgvector"} and self.is_sqlite:
            problems.append("MEMORY_PROVIDER=pgvector requires PostgreSQL, not SQLite")
        if self.voice_provider == "mock" or self.whisper_model == "mock":
            problems.append("VOICE_PROVIDER=mock")
        if self.voice_provider == "gemini" and not self.gemini_configured:
            problems.append("VOICE_PROVIDER=gemini but Gemini is not configured")
        if self.voice_provider == "sarvam" and not self.sarvam_api_key:
            problems.append("VOICE_PROVIDER=sarvam but SARVAM_API_KEY is empty")
        if self.workflow_engine == "local":
            problems.append("WORKFLOW_ENGINE=local (in-process loops, not n8n)")
        return problems

    @property
    def gemini_configured(self) -> bool:
        """Vertex needs a project; the developer API needs a key."""
        if self.gemini_backend == "vertex":
            return bool(self.google_cloud_project)
        return bool(self.gemini_api_key)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
