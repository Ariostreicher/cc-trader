"""Application settings — single source of truth, sourced from environment."""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Core
    ENVIRONMENT: str = "development"
    SECRET_KEY: str = "dev-secret-change-me"
    LOG_LEVEL: str = "INFO"

    # --- Database
    DATABASE_URL: str = "postgresql+asyncpg://cctrader:cctrader@postgres:5432/cctrader"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://cctrader:cctrader@postgres:5432/cctrader"

    # --- Redis
    REDIS_URL: str = "redis://redis:6379/0"

    # --- ChromaDB
    CHROMA_HOST: str = "chromadb"
    CHROMA_PORT: int = 8000

    # --- JWT
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # --- Single-user / demo mode
    # When true, ALL auth is bypassed: every API call is treated as if the
    # demo user made it. No login / registration required. Intended for
    # single-operator local use. NEVER enable this on a public deployment.
    DEV_NO_AUTH: bool = False
    # NOTE: do not use a .local / .test / .invalid / .example TLD here —
    # pydantic's email_validator rejects them.
    DEV_USER_EMAIL: str = "demo@cctrader.io"

    # --- LLM (OpenAI-compatible)
    # By default points at OpenAI. Switch the base URL + key + model trio to
    # use any OpenAI-compatible provider — Groq, Ollama, OpenRouter, Together,
    # DeepSeek, Mistral — without code changes. See docs/FREE_SETUP.md.
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = ""  # blank = OpenAI default
    OPENAI_MODEL_CHAT: str = "gpt-4o"
    OPENAI_MODEL_EMBED: str = "text-embedding-3-large"
    # When true, the LLM call drops `response_format={"type":"json_object"}`.
    # Some open-model providers (older Ollama, some OpenRouter routes) do not
    # accept that flag. The parser is forgiving — it strips ``` fences and
    # slices the first { ... } block, so JSON-mode is not strictly required.
    LLM_DISABLE_JSON_MODE: bool = False

    # --- Market data
    POLYGON_API_KEY: str = ""
    ALPACA_API_KEY: str = ""
    ALPACA_API_SECRET: str = ""
    ALPACA_BASE_URL: str = "https://paper-api.alpaca.markets"
    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    COINBASE_API_KEY: str = ""
    COINBASE_API_SECRET: str = ""

    # --- Email
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "no-reply@cctrader.local"

    # --- Stripe
    STRIPE_API_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_FREE: str = ""
    STRIPE_PRICE_PRO: str = ""
    STRIPE_PRICE_ENTERPRISE: str = ""

    # --- Frontend
    NEXT_PUBLIC_API_URL: str = "http://localhost:8000"
    NEXT_PUBLIC_WS_URL: str = "ws://localhost:8000"

    # --- CORS
    ALLOWED_ORIGINS: str = "http://localhost:3000"

    # --- Storage
    UPLOAD_DIR: str = "/data/uploads"
    MAX_UPLOAD_MB: int = 50

    @computed_field  # type: ignore[misc]
    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
