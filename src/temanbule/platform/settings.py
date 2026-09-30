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
    auth_frontend_success_url: str = "temanbule://profile"
    auth_frontend_error_url: str = "temanbule://login"
    auth_app_deep_link_scheme: str = "temanbule"
    auth_password_min_length: int = 8
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

    # --- CallCraft trusted HTTP adapter ---
    callcraft_base_url: str = "https://callcraft-api.flyup.id/v1"
    callcraft_user_id: str = ""
    callcraft_public_key: str = ""
    callcraft_auth: str = ""
    callcraft_project_id: str = ""
    callcraft_timeout_seconds: float = 15.0

    # --- Langflow run API (extraction/ingestion background) ---
    langflow_base_url: str = "http://localhost:7860"
    langflow_api_key: str = ""
    langflow_run_path: str = "/api/v2/workflows"
    langflow_timeout_seconds: float = 60.0
    langflow_connect_timeout_seconds: float = 5.0
    langflow_max_connections: int = 50

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
    otel_exporter_otlp_headers: str = ""
    otel_traces_sampler: str = "parentbased_traceidratio"
    otel_traces_sampler_arg: float = 1.0

    # --- Retention ---
    retention_idempotency_hours: int = 24
    retention_auth_session_days: int = 0
    retention_conversation_days: int = 0
    retention_media_days: int = 0
    retention_audit_days: int = 0
    retention_podcast_source_days: int = 0
    retention_podcast_audio_days: int = 0
    retention_financial_days: int = 0
    retention_webhook_days: int = 0

    # --- Embedding (dual projection; wajib bila FEATURE_DUAL_EMBEDDING_ENABLED) ---
    embedding_gemini_model: str = ""
    embedding_gemini_model_revision: str = ""
    embedding_gemini_dimension: int = 0
    embedding_gemini_task_type: str = ""
    embedding_openai_model: str = ""
    embedding_openai_model_revision: str = ""
    embedding_openai_dimension: int = 0
    embedding_openai_task_type: str = ""
    embedding_batch_size: int = 0
    embedding_max_attempts: int = 5

    # --- Astra DB vector projections (wajib bila FEATURE_DUAL_EMBEDDING_ENABLED) ---
    astra_db_api_endpoint: str = ""
    astra_db_application_token: str = ""
    astra_db_namespace: str = "default_keyspace"
    astra_request_timeout_seconds: float = 20.0

    # --- Media scan (wajib bila FEATURE_MEDIA_ENABLED) ---
    media_scan_service_url: str = ""
    media_scan_service_token: str = ""

    # --- S3-compatible media storage (wajib bila FEATURE_MEDIA_ENABLED) ---
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_force_path_style: bool = False
    s3_signed_url_ttl_seconds: int = 300
    s3_max_upload_bytes: int = 0

    # --- LiveKit / TTS (wajib bila FEATURE_REALTIME_CALL_ENABLED) ---
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_token_ttl_seconds: int = 300
    tts_provider: str = "elevenlabs"
    tts_api_key: str = ""
    tts_model: str = ""
    tts_voice_id_elean: str = ""
    tts_voice_id_willy: str = ""

    # --- Call / realtime mechanics ---
    call_vad_silence_ms: int = 0
    call_proactive_silence_seconds: int = 0
    call_max_duration_seconds: int = 0
    call_reconnect_grace_seconds: int = 0
    call_proactive_cooldown_seconds: int = 0
    call_proactive_max_prompts: int = 0
    realtime_max_concurrent_sessions: int = 0
    realtime_persistence_max_lag_seconds: int = 0
    realtime_lease_ttl_seconds: int = 0
    realtime_shutdown_grace_seconds: int = 0

    # --- Video frames (wajib bila FEATURE_VIDEO_CALL_ENABLED) ---
    video_frame_interval_ms: int = 0
    video_frame_max_width: int = 0
    video_frame_max_height: int = 0
    video_frame_max_bytes: int = 0
    video_frame_max_in_flight: int = 0
    video_frame_ttl_ms: int = 0

    # --- Podcast (wajib bila FEATURE_PODCAST_ENABLED) ---
    podcast_target_duration_seconds: int = 0
    podcast_max_extension_seconds: int = 0
    podcast_max_duration_seconds: int = 0
    podcast_idle_timeout_seconds: int = 0
    podcast_closing_grace_seconds: int = 0
    podcast_max_interruption_turns: int = 0
    podcast_max_upload_bytes: int = 0
    podcast_pdf_max_pages: int = 0
    podcast_pdf_max_expanded_bytes: int = 0
    podcast_parse_timeout_seconds: int = 0

    # --- VIP / background AI ---
    vip_stt_provider: str = ""
    vip_stt_base_url: str = ""
    vip_stt_api_key: str = ""
    vip_stt_model: str = ""
    vip_llm_provider: str = ""
    vip_llm_base_url: str = ""
    vip_llm_api_key: str = ""
    vip_llm_model: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    openai_base_url: str = "https://api.openai.com/v1"
    gemini_api_key: str = ""
    openai_api_key: str = ""
    background_ai_provider: str = ""
    background_ai_model: str = ""

    # --- Xendit billing (wajib bila FEATURE_BILLING_ENABLED) ---
    xendit_environment: str = "sandbox"
    xendit_base_url: str = "https://api.xendit.co"
    xendit_payment_product: str = ""
    xendit_api_version: str = ""
    xendit_secret_key: str = ""
    xendit_public_key: str = ""
    xendit_webhook_token: str = ""
    xendit_business_id: str = ""
    xendit_webhook_public_url: str = ""
    xendit_success_redirect_url: str = ""
    xendit_failure_redirect_url: str = ""
    xendit_timeout_seconds: int = 30
    xendit_payment_expiry_seconds: int = 0
    billing_reservation_window_seconds: int = 0
    billing_reservation_refill_threshold_percent: int = 0
    billing_usage_checkpoint_seconds: int = 0
    billing_reconciliation_interval_seconds: int = 0
    billing_unknown_usage_deadline_seconds: int = 0
    billing_max_concurrent_operations_per_user: int = 0

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

        # Feature: dual embedding — model/dimension kedua provider + batch
        if self.feature_dual_embedding_enabled:
            for name in (
                "EMBEDDING_GEMINI_MODEL",
                "EMBEDDING_GEMINI_MODEL_REVISION",
                "EMBEDDING_GEMINI_TASK_TYPE",
                "EMBEDDING_OPENAI_MODEL",
                "EMBEDDING_OPENAI_MODEL_REVISION",
                "EMBEDDING_OPENAI_TASK_TYPE",
                "GEMINI_API_KEY",
                "OPENAI_API_KEY",
                "ASTRA_DB_API_ENDPOINT",
                "ASTRA_DB_APPLICATION_TOKEN",
                "ASTRA_DB_NAMESPACE",
            ):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)
            for name in (
                "EMBEDDING_GEMINI_DIMENSION",
                "EMBEDDING_OPENAI_DIMENSION",
                "EMBEDDING_BATCH_SIZE",
            ):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)

        # Feature: media — scan service + S3 storage wajib
        if self.feature_media_enabled:
            for name in (
                "MEDIA_SCAN_SERVICE_URL",
                "MEDIA_SCAN_SERVICE_TOKEN",
                "S3_ENDPOINT_URL",
                "S3_REGION",
                "S3_BUCKET",
                "S3_ACCESS_KEY_ID",
                "S3_SECRET_ACCESS_KEY",
            ):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)
            for name in ("S3_SIGNED_URL_TTL_SECONDS", "S3_MAX_UPLOAD_BYTES"):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)
            if self.retention_media_days <= 0:
                problems.append("RETENTION_MEDIA_DAYS")

        # Feature: realtime call — LiveKit + TTS + batas session
        if self.feature_realtime_call_enabled:
            for name in (
                "LIVEKIT_URL",
                "LIVEKIT_API_KEY",
                "LIVEKIT_API_SECRET",
                "TTS_API_KEY",
                "TTS_MODEL",
                "TTS_VOICE_ID_ELEAN",
                "TTS_VOICE_ID_WILLY",
            ):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)
            for name in (
                "CALL_VAD_SILENCE_MS",
                "CALL_MAX_DURATION_SECONDS",
                "CALL_RECONNECT_GRACE_SECONDS",
                "REALTIME_MAX_CONCURRENT_SESSIONS",
                "REALTIME_LEASE_TTL_SECONDS",
            ):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)
            if not self.feature_media_enabled:
                problems.append("FEATURE_REALTIME_CALL_ENABLED memerlukan FEATURE_MEDIA_ENABLED")

        # Feature: video call — realtime + batas frame
        if self.feature_video_call_enabled:
            if not self.feature_realtime_call_enabled:
                problems.append(
                    "FEATURE_VIDEO_CALL_ENABLED memerlukan FEATURE_REALTIME_CALL_ENABLED"
                )
            for name in (
                "VIDEO_FRAME_INTERVAL_MS",
                "VIDEO_FRAME_MAX_WIDTH",
                "VIDEO_FRAME_MAX_HEIGHT",
                "VIDEO_FRAME_MAX_BYTES",
                "VIDEO_FRAME_MAX_IN_FLIGHT",
                "VIDEO_FRAME_TTL_MS",
            ):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)

        # Feature: podcast — realtime + dual embedding + batas durasi/parser
        if self.feature_podcast_enabled:
            if not self.feature_realtime_call_enabled:
                problems.append("FEATURE_PODCAST_ENABLED memerlukan FEATURE_REALTIME_CALL_ENABLED")
            if not self.feature_dual_embedding_enabled:
                problems.append("FEATURE_PODCAST_ENABLED memerlukan FEATURE_DUAL_EMBEDDING_ENABLED")
            for name in (
                "PODCAST_TARGET_DURATION_SECONDS",
                "PODCAST_MAX_DURATION_SECONDS",
                "PODCAST_IDLE_TIMEOUT_SECONDS",
                "PODCAST_CLOSING_GRACE_SECONDS",
                "PODCAST_MAX_UPLOAD_BYTES",
                "PODCAST_PDF_MAX_PAGES",
                "PODCAST_PDF_MAX_EXPANDED_BYTES",
                "PODCAST_PARSE_TIMEOUT_SECONDS",
            ):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)
            # Hard maximum harus lebih besar dari target + extension (blueprint env contract)
            if (
                self.podcast_max_duration_seconds > 0
                and self.podcast_target_duration_seconds + self.podcast_max_extension_seconds
                >= self.podcast_max_duration_seconds
            ):
                problems.append("PODCAST_MAX_DURATION_SECONDS harus > target + extension")

        # Feature: billing — Xendit produk/version/auth + BILLING limits
        if self.feature_billing_enabled:
            for name in (
                "XENDIT_PAYMENT_PRODUCT",
                "XENDIT_API_VERSION",
                "XENDIT_SECRET_KEY",
                "XENDIT_WEBHOOK_TOKEN",
                "XENDIT_BUSINESS_ID",
                "XENDIT_WEBHOOK_PUBLIC_URL",
            ):
                if _is_blank(getattr(self, name.lower())):
                    problems.append(name)
            for name in (
                "XENDIT_PAYMENT_EXPIRY_SECONDS",
                "BILLING_RESERVATION_WINDOW_SECONDS",
                "BILLING_USAGE_CHECKPOINT_SECONDS",
                "BILLING_RECONCILIATION_INTERVAL_SECONDS",
                "BILLING_UNKNOWN_USAGE_DEADLINE_SECONDS",
                "BILLING_MAX_CONCURRENT_OPERATIONS_PER_USER",
            ):
                if getattr(self, name.lower()) <= 0:
                    problems.append(name)
            # Refill strictly antara 0 dan 100; checkpoint < reservation window
            if not 0 < self.billing_reservation_refill_threshold_percent < 100:
                problems.append("BILLING_RESERVATION_REFILL_THRESHOLD_PERCENT harus (0, 100)")
            if (
                self.billing_usage_checkpoint_seconds > 0
                and self.billing_reservation_window_seconds > 0
                and self.billing_usage_checkpoint_seconds >= self.billing_reservation_window_seconds
            ):
                problems.append(
                    "BILLING_USAGE_CHECKPOINT_SECONDS harus < BILLING_RESERVATION_WINDOW_SECONDS"
                )
            if self.retention_financial_days <= 0:
                problems.append("RETENTION_FINANCIAL_DAYS")

        # Advance gratis: credential encryption wajib, checkout VIP tidak diperlukan.
        if self.feature_advance_enabled:
            if _is_blank(self.crypto_key_encryption_key):
                problems.append("CRYPTO_KEY_ENCRYPTION_KEY")

        # Feature: learn / toefl — dual embedding + background AI
        for flag, label in (
            (self.feature_learn_enabled, "FEATURE_LEARN_ENABLED"),
            (self.feature_toefl_enabled, "FEATURE_TOEFL_ENABLED"),
        ):
            if flag:
                if not self.feature_dual_embedding_enabled:
                    problems.append(f"{label} memerlukan FEATURE_DUAL_EMBEDDING_ENABLED")
                for name in ("BACKGROUND_AI_PROVIDER", "BACKGROUND_AI_MODEL"):
                    if _is_blank(getattr(self, name.lower())):
                        problems.append(name)

        # Feature: OTEL — endpoint collector eksplisit
        if self.feature_otel_enabled and _is_blank(self.otel_exporter_otlp_endpoint):
            problems.append("OTEL_EXPORTER_OTLP_ENDPOINT")

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
