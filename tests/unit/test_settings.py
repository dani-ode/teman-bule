"""Unit tests untuk settings validation (FND-02)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from temanbule.platform.settings import ConfigurationError, Settings


def _base_env(tmp_path: Path) -> dict[str, str]:
    priv = tmp_path / "auth_priv.pem"
    pub = tmp_path / "auth_pub.pem"
    epriv = tmp_path / "exec_priv.pem"
    epub = tmp_path / "exec_pub.pem"
    for p in (priv, pub, epriv, epub):
        p.write_text("placeholder")
    return {
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "AUTH_JWT_KEY_ID": "kid-1",
        "AUTH_JWT_PRIVATE_KEY_FILE": str(priv),
        "AUTH_JWT_PUBLIC_KEY_FILE": str(pub),
        "CRYPTO_EXECUTION_TOKEN_PRIVATE_KEY_FILE": str(epriv),
        "CRYPTO_EXECUTION_TOKEN_PUBLIC_KEY_FILE": str(epub),
        "MAIL_HOST": "localhost",
        "MAIL_FROM_ADDRESS": "no-reply@example.com",
        "FEATURE_AI_ENABLED": "false",
    }


def _settings_from_env(env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Settings:
    for key in list(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_missing_database_url_fails(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    del env["DATABASE_URL"]
    env["POSTGRES_PASSWORD"] = ""
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("DATABASE_URL" in p for p in exc_info.value.problems)


def test_missing_auth_keys_fails(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    del env["AUTH_JWT_PRIVATE_KEY_FILE"]
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("AUTH_JWT_PRIVATE_KEY_FILE" in p for p in exc_info.value.problems)


def test_missing_mail_host_fails(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    del env["MAIL_HOST"]
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("MAIL_HOST" in p for p in exc_info.value.problems)


def test_google_auth_requires_client_id(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    env["FEATURE_GOOGLE_AUTH_ENABLED"] = "true"
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("GOOGLE_CLIENT_ID" in p for p in exc_info.value.problems)


def test_ai_enabled_requires_m2m_tokens(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    env["FEATURE_AI_ENABLED"] = "true"
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("M2M_LANGFLOW_SERVICE_TOKEN" in p for p in exc_info.value.problems)


def test_production_requires_secure_cookie(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    env["APP_ENV"] = "production"
    env["AUTH_COOKIE_SECURE"] = "false"
    env["APP_ENABLE_DOCS"] = "false"
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("AUTH_COOKIE_SECURE" in p for p in exc_info.value.problems)


def test_valid_settings_pass(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    settings = _settings_from_env(env, monkeypatch)
    settings.validate_for_api()  # tidak raise


def test_cookie_samesite_invalid(tmp_path, monkeypatch):
    env = _base_env(tmp_path)
    env["AUTH_COOKIE_SAMESITE"] = "invalid"
    settings = _settings_from_env(env, monkeypatch)
    with pytest.raises(ConfigurationError) as exc_info:
        settings.validate_for_api()
    assert any("AUTH_COOKIE_SAMESITE" in p for p in exc_info.value.problems)
