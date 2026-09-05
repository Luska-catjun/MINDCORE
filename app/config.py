from functools import lru_cache
import json
from typing import List
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MindCore"
    # Public product defaults are intentionally persona-neutral. A private
    # developer profile can opt into a different identity file without making
    # that identity part of the desktop bundle.
    persona_display_name: str = "Persona"
    persona_identity_path: str | None = None
    environment: str = Field(
        default="local",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    private_access_password: str | None = None
    auth_signing_secret: str | None = None
    auth_cookie_samesite: str = "lax"
    auth_cookie_domain: str | None = None
    auth_session_max_age_seconds: int = Field(default=60 * 60 * 24 * 30, gt=0)
    auth_bearer_session_max_age_seconds: int = Field(default=60 * 60 * 8, gt=0)
    diana_timezone: str = "Asia/Seoul"
    supabase_url: str | None = None
    supabase_key: str | None = None
    supabase_db_url: str | None = Field(
        default=None,
        description="PostgreSQL connection string from Supabase project settings.",
    )
    # Turso is the verified default.  Set DATABASE_BACKEND=supabase explicitly
    # only for the retained rollback path.
    database_backend: str = "turso"
    database_url: str | None = None
    database_auth_token: str | None = None
    old_database_url: str | None = None
    turso_db_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("Turso_DB_URL", "TURSO_DB_URL"),
        description="Legacy Turso URL accepted during the dual-DB migration.",
    )
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_thinking_level: str = "minimal"
    gemini_timeout_seconds: float = Field(default=45.0, gt=0)
    gemini_max_retries: int = Field(default=1, ge=0, le=5)
    llm_provider: str = "gemini"
    llm_fallback_provider: str | None = "groq"
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.6-27b"
    groq_api_base_url: str = "https://api.groq.com/openai/v1"
    groq_timeout_seconds: float = Field(default=45.0, gt=0)
    groq_max_retries: int = Field(default=2, ge=0, le=5)
    cors_origins: List[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ]
    @field_validator("auth_cookie_samesite")
    @classmethod
    def validate_auth_cookie_samesite(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"lax", "strict", "none"}:
            raise ValueError("AUTH_COOKIE_SAMESITE must be lax, strict, or none.")
        return normalized

    @field_validator("gemini_thinking_level")
    @classmethod
    def validate_gemini_thinking_level(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"minimal", "low", "medium", "high"}:
            raise ValueError("GEMINI_THINKING_LEVEL must be minimal, low, medium, or high.")
        return normalized

    @field_validator("diana_timezone")
    @classmethod
    def validate_diana_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("DIANA_TIMEZONE must be a valid IANA timezone.") from exc
        return value

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        enable_decoding=False,
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                pass
        if isinstance(value, list):
            origins = [
                origin.strip()
                for item in value
                for origin in str(item).split(",")
                if origin.strip()
            ]
        else:
            origins = [origin.strip() for origin in value.split(",") if origin.strip()]
        # The native webview has a fixed local origin. Preserve every web
        # deployment origin while allowing only the two Tauri local forms.
        for origin in ("http://127.0.0.1:1420", "tauri://localhost", "http://tauri.localhost"):
            if origin not in origins:
                origins.append(origin)
        return origins


@lru_cache
def get_settings() -> Settings:
    # M2's sidecar receives this path from Tauri's cross-platform app config
    # directory. Source/web development keeps the existing local `.env`
    # default. The path is never embedded in the product bundle.
    from os import environ

    return Settings(_env_file=environ.get("MINDCORE_ENV_FILE", ".env"))
