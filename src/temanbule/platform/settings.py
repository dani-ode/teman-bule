"""Typed settings loader (FND-02).

Seluruh konfigurasi divalidasi saat startup. Required value yang kosong menghasilkan
ConfigurationError yang menyebutkan nama variable saja — tanpa nilai/secret.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SAME_SITE_VALUES = {"lax", "strict", "none"}


class ConfigurationError(RuntimeError):
    """Konfigurasi wajib kosong/tidak valid; menyebut variable names only."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Konfigurasi tidak valid: " + ", ".join(problems))


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: str = "development"
    app_name: str = "temanbule-backend"
    app_host: str = "0.0.0.0"  # noqa: S104 - bind host adalah konfigurasi deployment
    app_port: int = 8000
    app_public_url: str = "http://localhost:8000"
    app_internal_base_url: str = ""
    app_log_level: str = "INFO"
    app_cors_origins: str = "http://localhost:3000"
    app_trusted_proxy_ips: str = "127.0.0.1"
    app_enable_docs: bool = True
    app_max_request_bytes: int = 1048576

    # --- Feature flags ---
    feature_ai_enabled: bool = True
    feature_learn_enabled: bool = False
    feature_toefl_enabled: bool = False
    feature_media_enabled: bool = False
    feature_realtime_call_enabled: bool = False
    feature_otel_enabled: bool = False
    feature_google_auth_enabled: bool = False
    feature_billing_enabled: bool = False
    feature_advance_enabled: bool = False
    feature_video_call_enabled: bool = False
    feature_podcast_enabled: bool = False
    feature_dual_embedding_enabled: bool = False

    # --- Database / Redis ---
    postgres_db: str = "temanbule"
    postgres_user: str = "temanbule"
    postgres_password: str = ""
    database_url: str = ""
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_seconds: int = 10
    database_statement_timeout_ms: int = 30000
    redis_url: str = "redis://redis:6379/0"
    redis_outbox_stream: str = "temanbule:outbox"
    redis_dead_letter_stream: str = "temanbule:dead-letter"
    redis_consumer_group: str = "temanbule-workers"
    redis_consumer_name: str = "worker-local-1"
    redis_block_ms: int = 5000
    redis_claim_idle_ms: int = 60000
    redis_max_attempts: int = 5

    # --- Auth ---
    auth_jwt_issuer: str = "temanbule-backend"
    auth_jwt_audience: str = "temanbule-client"
    auth_jwt_algorithm: str = "RS256"
    auth_jwt_key_id: str = ""
    auth_jwt_private_key_file: str = ""
    auth_jwt_public_key_file: str = ""
    auth_access_token_ttl_seconds: int = 900
    auth_refresh_token_ttl_seconds: int = 2592000
    auth_action_token_ttl_seconds: int = 1800
    auth_oauth_transaction_ttl_seconds: int = 600
    auth_jwks_cache_seconds: int = 300
    auth_clock_skew_seconds: int = 30
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_cookie_domain: str = ""
    auth_frontend_success_url: str = "http://localhost:3000/profile"
    auth_frontend_error_url: str = "http://localhost:3000/login"
    auth_password_min_length: int = 12
    auth_argon2_memory_kib: int = 65536
    auth_argon2_time_cost: int = 3
    auth_argon2_parallelism: int = 1
    auth_rate_limit_per_ip_per_minute: int = 10

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/v1/auth/google/callback"
    google_discovery_url: str = "https://accounts.google.com/.well-known/openid-configuration"

    # --- Crypto / M2M ---
    crypto_key_encryption_key: str = ""
    crypto_kms_key_id: str = ""
    crypto_key_version: int = 1
    crypto_execution_token_private_key_file: str = ""
    crypto_execution_token_public_key_file: str = ""
    crypto_execution_token_issuer: str = "temanbule-backend"  # noqa: S105 - issuer identifier
    crypto_execution_token_audience: str = "temanbule-ai-runtime"  # noqa: S105 - audience identifier
    crypto_execution_token_ttl_seconds: int = 60
    m2m_langflow_service_token: str = ""
    m2m_callcraft_service_token: str = ""
    m2m_realtime_service_token: str = ""
    m2m_token_header: str = "X-Service-Token"  # noqa: S105 - header name
    m2m_max_clock_skew_seconds: int = 10

    # --- Mail ---
    mail_mailer: str = "smtp"
    mail_host: str = ""
    mail_port: int = 465
    mail_username: str = ""
    mail_password: str = ""
    mail_encryption: Literal["implicit_tls", "starttls", "plain"] = "implicit_tls"
    mail_from_address: str = ""
    mail_from_name: str = "Teman Bule"

    # --- OTEL (readiness saja; instrumentation pada fase berikutnya) ---
    otel_service_name: str = "temanbule-backend"
    otel_exporter_otlp_endpoint: str = ""
    otel_traces_sampler: str = "parentbased_traceidratio"
    otel_traces_sampler_arg: float = 1.0

    # --- Retention ---
    retention_idempotency_hours: int = 24
    retention_auth_session_days: int = 0

    @field_validator("app_cors_origins", "app_trusted_proxy_ips")
    @classmethod
    def _non_empty_list(cls, value: str) -> str:
        if value.strip() == "":
            msg = "tidak boleh kosong"
            raise ValueError(msg)
        return value

    # --- Helpers ---
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.app_cors_origins.split(",") if item.strip()]

    def effective_database_url(self) -> str:
        if not _is_blank(self.database_url):
            return self.database_url
        if not _is_blank(self.postgres_password):
            return (
                f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@postgres:5432/{self.postgres_db}"
            )
        return ""

    def production_mode(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    def validate_for_api(self) -> None:
        """Validasi feature matrix; raise ConfigurationError bila ada masalah."""
        problems: list[str] = []

        if _is_blank(self.effective_database_url()):
            problems.append("DATABASE_URL atau POSTGRES_PASSWORD wajib diisi")
        if _is_blank(self.redis_url):
            problems.append("REDIS_URL")

        # Auth keys
        for name in ("AUTH_JWT_KEY_ID", "AUTH_JWT_PRIVATE_KEY_FILE", "AUTH_JWT_PUBLIC_KEY_FILE"):
            if _is_blank(getattr(self, name.lower())):
                problems.append(name)
        for name in (
            "CRYPTO_EXECUTION_TOKEN_PRIVATE_KEY_FILE",
            "CRYPTO_EXECUTION_TOKEN_PUBLIC_KEY_FILE",
        ):
            if _is_blank(getattr(self, name.lower())):
                problems.append(name)

        # Private key files harus ada (tidak membaca isi ke log)
        for label, path in (
            ("AUTH_JWT_PRIVATE_KEY_FILE", self.auth_jwt_private_key_file),
            ("AUTH_JWT_PUBLIC_KEY_FILE", self.auth_jwt_public_key_file),
            (
                "CRYPTO_EXECUTION_TOKEN_PRIVATE_KEY_FILE",
                self.crypto_execution_token_private_key_file,
            ),
            (
                "CRYPTO_EXECUTION_TOKEN_PUBLIC_KEY_FILE",
                self.crypto_execution_token_public_key_file,
            ),
        ):
            if not _is_blank(path) and not Path(path).is_file():
                problems.append(f"{label} (file tidak ditemukan)")

        # SMTP wajib untuk registration/reset
        if _is_blank(self.mail_host):
            problems.append("MAIL_HOST")
        if _is_blank(self.mail_from_address):
            problems.append("MAIL_FROM_ADDRESS")

        # Feature: Google auth
        if self.feature_google_auth_enabled:
            for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REDIRECT_URI"):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)

        # M2M tokens bila AI aktif
        if self.feature_ai_enabled:
            for name in (
                "M2M_LANGFLOW_SERVICE_TOKEN",
                "M2M_CALLCRAFT_SERVICE_TOKEN",
                "M2M_REALTIME_SERVICE_TOKEN",
            ):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)

        if self.auth_password_min_length < 8:
            problems.append("AUTH_PASSWORD_MIN_LENGTH minimal 8")
        if self.auth_cookie_samesite.lower() not in _SAME_SITE_VALUES:
            problems.append("AUTH_COOKIE_SAMESITE harus lax|strict|none")
        if self.production_mode() and not self.auth_cookie_secure:
            problems.append("AUTH_COOKIE_SECURE wajib true di production")
        if self.production_mode() and self.app_enable_docs:
            problems.append("APP_ENABLE_DOCS wajib false di production")
        if not 0.0 < float(self.otel_traces_sampler_arg) <= 1.0:
            problems.append("OTEL_TRACES_SAMPLER_ARG harus dalam (0, 1]")

        if problems:
            raise ConfigurationError(problems)


def load_settings(*, validate: bool = True) -> Settings:
    settings = Settings()
    if validate:
        settings.validate_for_api()
    return settings


def redacted_settings_snapshot(settings: Settings) -> str:
    """Diagnostic aman: hanya nama grup dan status terisi/kosong, tanpa nilai."""
    groups: dict[str, bool] = {
        "database": not _is_blank(settings.effective_database_url()),
        "redis": not _is_blank(settings.redis_url),
        "auth_keys": not _is_blank(settings.auth_jwt_private_key_file),
        "execution_keys": not _is_blank(settings.crypto_execution_token_private_key_file),
        "smtp": not _is_blank(settings.mail_host),
        "google_auth": not _is_blank(settings.google_client_id),
        "m2m": not _is_blank(settings.m2m_langflow_service_token),
    }
    return json.dumps({"groups": groups}, sort_keys=True)
