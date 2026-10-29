"""Environment-based configuration.

All environment-specific values (database location, secrets, deployment mode)
come from environment variables or a `.env` file. Business logic must never read
paths or credentials directly; it receives them through `get_settings()`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EGI_",
        env_file=(BACKEND_DIR / ".env", BACKEND_DIR.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "hosted", "test"] = "local"
    # SQLite by default for local use; set a postgresql+psycopg:// URL when hosted.
    database_url: str = Field(default_factory=lambda: f"sqlite:///{BACKEND_DIR / 'data' / 'egi.db'}",
                              validation_alias=AliasChoices("EGI_DATABASE_URL", "DATABASE_URL"))

    # Fernet key used to encrypt integration credentials at rest.
    # Generate with: uv run python -m app.cli generate-key
    secret_key: SecretStr | None = None

    log_level: str = "INFO"
    log_json: bool = False

    # Background scheduler for automatic synchronisation.
    scheduler_enabled: bool = True

    # Authentication. "none" is only acceptable for local, single-user use.
    auth_mode: Literal["none", "password"] = "none"
    auth_password: SecretStr | None = None
    session_ttl_hours: int = 12

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Optional AI narrative layer. Deterministic analysis works without it.
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-opus-5-5"

    shopify_api_version: str = "2026-10"

    # Built frontend served by the backend in hosted mode.
    frontend_dist: Path = BACKEND_DIR.parent / "frontend" / "dist"

    @field_validator("database_url")
    @classmethod
    def _driver(cls, v: str) -> str:
        # Neon, Supabase and Render give "postgres(ql)://..." URLs; use the installed psycopg 3 driver.
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @property
    def serverless(self) -> bool:
        """Running as short-lived functions (Vercel): no background threads or in-process scheduler."""
        import os

        return bool(os.environ.get("VERCEL"))

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()
