"""Typed application configuration.

All configuration comes from environment variables (or a local .env file). Nothing
in the codebase reads os.environ directly; everything depends on `Settings`.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    PRODUCTION = "production"


class LLMProviderName(StrEnum):
    GEMINI = "gemini"
    GROQ = "groq"
    OLLAMA = "ollama"


class RateLimitConfig(BaseModel):
    """Free-tier quota for one provider:model. `None` means unlimited."""

    rpm: int | None = Field(default=None, gt=0, description="Requests per minute")
    rpd: int | None = Field(default=None, gt=0, description="Requests per rolling 24h")
    tpm: int | None = Field(default=None, gt=0, description="Tokens per minute")


# Conservative defaults for free tiers (verify in AI Studio / Groq console; override
# via LLM_RATE_LIMITS). Keys are "<provider>:<model>".
# Gemini free-tier daily limits are small and per model (e.g. 20/day for 3.5 Flash), so
# RPD is left to the server: its 429 carries the exact retry delay, which the router
# honours. Groq publishes 1000 RPD / 8000 TPM per model on the free tier.
_GEMINI_REASONING = ("gemini-3.5-flash", "gemini-2.5-flash", "gemini-3.7-flash", "gemini-3.8-flash")
_GEMINI_FAST = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-flash-lite-latest")
_GROQ = ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")
_DEFAULT_RATE_LIMITS: dict[str, RateLimitConfig] = {
    **{f"gemini:{m}": RateLimitConfig(rpm=5, tpm=250_000) for m in _GEMINI_REASONING},
    **{f"gemini:{m}": RateLimitConfig(rpm=10, tpm=250_000) for m in _GEMINI_FAST},
    **{f"groq:{m}": RateLimitConfig(rpm=30, rpd=1000, tpm=8_000) for m in _GROQ},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Repo-root .env (used by docker compose) and an optional backend/.env override.
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        # `GEMINI_API_KEY=` in .env means "not configured", not an empty secret.
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    # --- application -------------------------------------------------------------
    app_name: str = "RegTrace"
    environment: Environment = Environment.LOCAL
    log_level: str = "INFO"
    log_json: bool = True
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- database ----------------------------------------------------------------
    # The app connects as a least-privilege role that is subject to row-level
    # security; migrations run as the owner role.
    database_url: str = "postgresql+asyncpg://regtrace_app:regtrace_app@localhost:5432/regtrace"
    migration_database_url: str = "postgresql+asyncpg://regtrace:regtrace@localhost:5432/regtrace"
    app_db_password: SecretStr = SecretStr("regtrace_app")
    db_pool_size: int = Field(default=5, gt=0)
    db_connect_timeout_s: float = Field(default=5.0, gt=0)

    # --- security ----------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("dev-only-change-me-dev-only-change-me")
    jwt_ttl_minutes: int = Field(default=480, gt=0)

    # --- storage / ingestion -----------------------------------------------------
    storage_dir: str = "data/uploads"
    # Directory holding synthetic/ and golden/ (demo corpus + evaluation labels).
    data_dir: str | None = None
    max_upload_mb: int = Field(default=50, gt=0)
    max_pdf_pages: int = Field(default=500, gt=0)

    # --- retrieval ---------------------------------------------------------------
    embedding_backend: str = "fastembed"  # fastembed | hashing (tests / offline)
    embedding_model: str = "BAAI/bge-base-en-v1.5"
    reranker_backend: str = "fastembed"  # fastembed | none
    reranker_model: str = "BAAI/bge-reranker-base"
    model_cache_dir: str = "data/models"
    retrieval_min_rerank_score: float = 0.2
    retrieval_min_fused_score: float = 0.01

    # Optional webhook (Slack/Teams-compatible JSON) for impact-report notifications.
    notify_webhook_url: str | None = None

    # --- cost accounting -----------------------------------------------------------
    # Actual spend is $0 (free tiers). These USD-per-million-token prices estimate
    # what the same traffic would cost on a paid mid-tier / small model.
    equivalent_price_reasoning_in: float = 2.0
    equivalent_price_reasoning_out: float = 10.0
    equivalent_price_fast_in: float = 1.0
    equivalent_price_fast_out: float = 5.0

    # --- worker ------------------------------------------------------------------
    # The job worker runs inside the API process by default (single process = one
    # accurate view of free-tier quota). Set false and run `python -m app.workers`
    # separately to scale out.
    worker_in_process: bool = True
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    worker_step_timeout_s: float = Field(default=900.0, gt=0)

    # --- LLM routing -------------------------------------------------------------
    # Ordered fallback chains per tier. A provider without credentials is skipped.
    llm_reasoning_chain: list[LLMProviderName] = [
        LLMProviderName.GEMINI,
        LLMProviderName.GROQ,
        LLMProviderName.OLLAMA,
    ]
    llm_fast_chain: list[LLMProviderName] = [
        LLMProviderName.GEMINI,
        LLMProviderName.GROQ,
        LLMProviderName.OLLAMA,
    ]
    llm_request_timeout_s: float = Field(default=90.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=5)
    llm_backoff_base_s: float = Field(default=1.0, ge=0)
    # If a local quota window frees up within this many seconds we wait; otherwise
    # we fall through to the next provider.
    llm_max_rate_wait_s: float = Field(default=15.0, ge=0)
    # When every provider is throttled, wait up to this long for the soonest window.
    llm_max_queue_wait_s: float = Field(default=90.0, ge=0)
    llm_breaker_failure_threshold: int = Field(default=3, gt=0)
    llm_breaker_cooldown_s: float = Field(default=60.0, gt=0)
    llm_rate_limits: dict[str, RateLimitConfig] = Field(default_factory=lambda: dict(_DEFAULT_RATE_LIMITS))

    # --- Gemini (free tier via Google AI Studio) ---------------------------------
    gemini_api_key: SecretStr | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_reasoning_model: str = "gemini-3.5-flash"
    gemini_fast_model: str = "gemini-3.5-flash-lite"
    # Extra models tried in order when the primary one is rate-limited (free-tier quotas are per model).
    gemini_reasoning_fallback_models: list[str] = ["gemini-2.5-flash", "gemini-3.7-flash", "gemini-3.8-flash"]
    gemini_fast_fallback_models: list[str] = ["gemini-3.1-flash-lite", "gemini-flash-lite-latest"]
    # Thinking models spend output tokens on reasoning; Gemini requests get at least this budget.
    gemini_min_output_tokens: int = Field(default=16384, gt=0)
    # Send the Pydantic JSON schema natively (responseJsonSchema). Disable if the
    # chosen model rejects it; the schema is then embedded in the prompt instead.
    gemini_native_json_schema: bool = True
    # None = model default. 0 disables "thinking" on 2.5 Flash (saves output quota).
    gemini_thinking_budget: int | None = None

    # --- Groq (free tier, OpenAI-compatible) -------------------------------------
    groq_api_key: SecretStr | None = None
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_reasoning_model: str = "openai/gpt-oss-120b"
    groq_fast_model: str = "openai/gpt-oss-20b"
    groq_reasoning_fallback_models: list[str] = ["qwen/qwen3.8-27b"]
    groq_fast_fallback_models: list[str] = []

    # --- Ollama (local, unlimited, OpenAI-compatible) ----------------------------
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_reasoning_model: str = "qwen2.5:7b"
    ollama_fast_model: str = "qwen2.5:3b"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
