"""Identity module models (FND-06/07/08). Sesuai postgresql-schema.md."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from temanbule.platform.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    normalized_email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|disabled|deleted
    auth_epoch: Mapped[int] = mapped_column(Integer, default=0)
    locale: Mapped[str | None] = mapped_column(String(20))
    timezone: Mapped[str | None] = mapped_column(String(64))

    password_credential: Mapped[PasswordCredential | None] = relationship(back_populates="user")


class PasswordCredential(Base):
    __tablename__ = "password_credentials"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(Text)
    algorithm: Mapped[str] = mapped_column(String(40), default="argon2id")
    params_version: Mapped[int] = mapped_column(Integer, default=1)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="password_credential")


class AuthIdentity(Base, TimestampMixin):
    __tablename__ = "auth_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_auth_identities_provider_subject"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(20))  # google
    subject: Mapped[str] = mapped_column(String(255))


class AuthSession(Base):
    """Refresh session family; hash-only refresh token."""

    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(26), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(26))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    auth_epoch: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuthActionToken(Base):
    """Single-use purpose-bound token (verify email / reset password), hash-only."""

    __tablename__ = "auth_action_tokens"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(40))  # email_verify | password_reset
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OauthTransaction(Base):
    """State/nonce/PKCE browser transaction untuk Google OIDC."""

    __tablename__ = "oauth_transactions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    encrypted_pkce_verifier: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(String(10))  # login | link
    bound_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    browser_binding_hash: Mapped[str] = mapped_column(String(64))
    redirect_ref: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserProfile(Base, TimestampMixin):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str | None] = mapped_column(String(120))
    english_level: Mapped[str | None] = mapped_column(String(20))
    learning_goals: Mapped[str | None] = mapped_column(Text)  # JSONB pada fase berikutnya
    preferences: Mapped[str | None] = mapped_column(Text)
