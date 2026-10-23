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
async def test_register_duplicate_rejected(session):
    """Register ulang dengan email yang sama → 409 Conflict eksplisit."""
    settings = _make_settings()
    auth = AuthService(session, settings)
    email = "test-duplicate@example.com"
    await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture

    from temanbule.platform.errors import ConflictError

    with pytest.raises(ConflictError, match="sudah terdaftar"):
        await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")
async def test_login_unverified_rejected(session):
    settings = _make_settings()
    auth = AuthService(session, settings)
    email = "test-unverified@example.com"
    await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture
    from temanbule.platform.errors import UnauthorizedError

    with pytest.raises(UnauthorizedError, match="belum diverifikasi"):
        await auth.login(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture


def _oauth_txn():
    """OauthTransaction dummy untuk complete_google_login."""
    from datetime import UTC, datetime, timedelta

    from temanbule.modules.identity.models import OauthTransaction
    from temanbule.platform.security import new_ulid

    return OauthTransaction(
        id=new_ulid(),
        state_hash="s" * 64,
        nonce_hash="n" * 64,
        encrypted_pkce_verifier="v",
        intent="login",
        bound_user_id=None,
        browser_binding_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        created_at=datetime.now(UTC),
    )


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")
async def test_google_login_verified_email_conflict(session):
    """Email verified + subject Google baru → GOOGLE_LINK_REQUIRED (no auto-link)."""
    from datetime import UTC, datetime

    from temanbule.modules.identity.models import User
    from temanbule.platform.errors import ConflictError
    from temanbule.platform.security import new_ulid

    settings = _make_settings()
    auth = AuthService(session, settings)
    user = User(
        id=new_ulid(),
        normalized_email="glink-verified@example.com",
        email_verified_at=datetime.now(UTC),
        status="active",
        auth_epoch=0,
    )
    session.add(user)
    await session.flush()

    with pytest.raises(ConflictError) as exc_info:
        await auth.complete_google_login(
            txn=_oauth_txn(),
            google_subject="google-sub-conflict",
            google_email="glink-verified@example.com",
        )
    assert exc_info.value.code == "GOOGLE_LINK_REQUIRED"


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")
async def test_google_login_unverified_email_auto_links(session):
    """Email belum verified + Google login → tautkan identitas, verifikasi, dan login."""
    settings = _make_settings()
    auth = AuthService(session, settings)
    email = "glink-unverified@example.com"
    await auth.register(email=email, password="supersecretpassword1")  # noqa: S106 - test fixture

    from temanbule.modules.identity.repository import IdentityRepository

    issued = await auth.complete_google_login(
        txn=_oauth_txn(),
        google_subject="google-sub-autolink",
        google_email=email,
    )
    assert issued.user_id
    assert issued.refresh_token

    repo = IdentityRepository(session)
    user = await repo.get_user_by_email(email)
    assert user is not None
    assert user.email_verified_at is not None
    identity = await repo.get_identity("google", "google-sub-autolink")
    assert identity is not None
    assert identity.user_id == user.id
