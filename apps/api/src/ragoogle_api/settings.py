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
    rerank_model: str = "rerank-2.5"

    context_window: int = Field(default=200_000, ge=1000)
    reserved_for_response: int = Field(default=8_192, ge=256)

    cors_origins: str = "http://localhost:5173,http://localhost:4173"
    otel_endpoint: str | None = None
    log_level: str = "INFO"

    # ADR-0006: the topology app polls these to ping the frontends' own web
    # servers. Defaults match the docker-compose service DNS names; unset
    # (empty) means "don't probe" -- the node shows unknown rather than a
    # guessed status, honest about not having checked.
    frontend_url: str | None = "http://frontend"
    observability_url: str | None = "http://observability"

    # Ping targets for the two nodes that were permanently "unknown" -- Claude
    # and Voyage have no health API a customer can call, so these point at the
    # closest honest proxy: each vendor's public marketing site, which answers
    # 200 directly with no redirect. (status.anthropic.com was tried first and
    # rejected: it 301s on first hit, and _ping treats a redirect as down by
    # design -- see test_a_redirect_is_not_treated_as_success -- so it always
    # read as down regardless of Anthropic's actual status.) Either can be
    # unset to fall back to "unknown" rather than a guess, same as
    # frontend_url/observability_url.
    anthropic_ping_url: str | None = "https://www.anthropic.com"
    voyage_ping_url: str | None = "https://www.voyageai.com"

    # ADR-0016. A real Google OAuth consent flow, not a "paste a token you
    # already have" text box. `google_oauth_redirect_uri` must be registered
    # verbatim as an authorized redirect URI on the OAuth client in Google
    # Cloud Console -- Google refuses the exchange otherwise. All three are
    # None until the operator sets up a project; the flow returns a clear 503
    # rather than a confusing failure deep in the exchange when they are.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_uri: str = "http://localhost:8000/oauth/google/callback"
    # Where the browser is sent *back to* once the callback finishes -- the
    # user-facing origin, not frontend_url above (that one is the container-
    # network hostname used for the topology ping and is unreachable from an
    # actual browser).
    frontend_public_url: str = "http://localhost:5173"

    # Same distinction as frontend_public_url, for the observability app's own
    # node, and the API's own node (linked to its interactive docs rather than
    # its bare origin, since that is what a human clicking it actually wants).
    observability_public_url: str = "http://localhost:5174"
    api_public_url: str = "http://localhost:8000/docs"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
