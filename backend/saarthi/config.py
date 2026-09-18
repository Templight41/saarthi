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
    database_url: str = DEFAULT_SQLITE_URL
    cors_origins: str = "http://localhost:5173"
    internal_api_token: str = "dev-internal-token"
    seed_on_startup: bool = True

    # --- LLM ---
    llm_provider: Literal["gemini", "sarvam", "mock"] = "mock"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    sarvam_api_key: str = ""
    sarvam_model: str = "sarvam-105b"
    llm_timeout_seconds: float = 20.0
    diagnosis_confidence_threshold: float = 0.6

    # --- Memory ---
    memory_provider: Literal["auto", "pgvector", "local"] = "auto"
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 768
    memory_top_k: int = 5
    memory_timeout_seconds: float = 3.0

    # --- Workflows ---
    workflow_engine: Literal["auto", "n8n", "local"] = "auto"
    n8n_base_url: str = "http://localhost:5678"
    n8n_webhook_url: str = "http://localhost:5678/webhook"
    scheduled_refund_wait_seconds: int = 20
    scheduled_refund_threshold_seconds: int = 60
    scheduled_refund_max_checks: int = 3
    settlement_monitor_interval_seconds: int = 30
    settlement_delay_grace_seconds: int = 900
    alert_ttl_minutes: int = 30

    # --- Voice ---
    voice_provider: Literal["auto", "sarvam", "faster_whisper", "mock"] = "auto"
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

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
