"""Configuration.

Everything is environment-driven so the same image runs in every environment,
and so the Terraform modules (ADR-0005) have one contract to satisfy.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAGOOGLE_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://ragoogle:ragoogle@localhost:5433/ragoogle"

    # ADR-0002. The dimension must match the deployed pgvector column; the
    # startup check compares them and refuses to serve if they disagree.
    embedding_model: str = "voyage-3-large"
    embedding_dimensions: int = 1024
    voyage_api_key: str | None = None

    anthropic_api_key: str | None = None

    # ADR-0003. The data key for credential encryption. Absent, source
    # registration still works but ingestion cannot start -- refusing to hold
    # Drive credentials in plaintext is not negotiable.
    credential_secret: str | None = None
    default_chat_model: str = "claude-opus-5"

    # ADR-0004 defaults.
    retrieval_limit: int = Field(default=8, ge=1, le=50)
    candidate_limit: int = Field(default=50, ge=1, le=500)
    rrf_k: int = Field(default=60, ge=1)
    rerank_enabled: bool = True

    context_window: int = Field(default=200_000, ge=1000)
    reserved_for_response: int = Field(default=8_192, ge=256)

    cors_origins: str = "http://localhost:5173,http://localhost:4173"
    otel_endpoint: str | None = None
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
