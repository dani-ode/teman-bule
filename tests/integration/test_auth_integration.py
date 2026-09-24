"""Integration tests untuk auth flow (FND-06/07). Memerlukan PostgreSQL nyata."""

from __future__ import annotations

import os

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from temanbule.modules.identity.auth_service import AuthService, normalize_email
from temanbule.platform.settings import Settings

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")


def _make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=DATABASE_URL,
        auth_jwt_private_key_file=os.environ.get("AUTH_JWT_PRIVATE_KEY_FILE", ""),
        auth_jwt_public_key_file=os.environ.get("AUTH_JWT_PUBLIC_KEY_FILE", ""),
        auth_jwt_key_id=os.environ.get("AUTH_JWT_KEY_ID", "kid-1"),
        mail_host="localhost",
        mail_from_address="no-reply@example.com",
        feature_ai_enabled=False,
    )


@pytest.fixture()
async def session():
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL tidak diset")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_normalize_email():
    from temanbule.platform.errors import ValidationError

    assert normalize_email("  User@Example.COM ") == "user@example.com"
    with pytest.raises(ValidationError):
        normalize_email("not-an-email")


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")
async def test_register_and_verify_email(session):
    settings = _make_settings()
    auth = AuthService(session, settings)
    email = "test-register@example.com"
    await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture
    # User harus ada tapi belum verified
    from temanbule.modules.identity.repository import IdentityRepository

    repo = IdentityRepository(session)
    user = await repo.get_user_by_email(email)
    assert user is not None
    assert user.email_verified_at is None


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")
async def test_login_unverified_rejected(session):
    settings = _make_settings()
    auth = AuthService(session, settings)
    email = "test-unverified@example.com"
    await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture
    from temanbule.platform.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError, match="belum diverifikasi"):
        await auth.login(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture
