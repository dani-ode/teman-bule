"""Auth service (FND-06/07/08): register, verify, login, refresh rotation, reset, Google."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.identity.models import (
    AuthActionToken,
    AuthIdentity,
    AuthSession,
    OauthTransaction,
    PasswordCredential,
    User,
    UserProfile,
)
from temanbule.modules.identity.repository import IdentityRepository
from temanbule.modules.reliability.audit import record_audit
from temanbule.modules.reliability.outbox import record_outbox_event
from temanbule.platform.errors import ConflictError, UnauthorizedError, ValidationError
from temanbule.platform.security import (
    base64url_no_pad,
    hash_password,
    issue_access_jwt,
    make_password_hasher,
    new_opaque_token,
    new_ulid,
    sha256_hex,
    verify_password,
)
from temanbule.platform.settings import Settings


def normalize_email(email: str) -> str:
    local, _, domain = email.strip().partition("@")
    if not local or not domain:
        raise ValidationError("Format email tidak valid.")
    return f"{local.lower()}@{domain.lower()}"


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    family_id: str
    user_id: str


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repo = IdentityRepository(session)
        self._hasher = make_password_hasher(
            memory_kib=settings.auth_argon2_memory_kib,
            time_cost=settings.auth_argon2_time_cost,
            parallelism=settings.auth_argon2_parallelism,
        )

    # --- Register (FND-06) ---

    async def register(
        self, *, email: str, password: str, correlation_id: str | None = None
    ) -> None:
        """Buat account unverified + token verifikasi. Response selalu generik."""
        normalized = normalize_email(email)
        if len(password) < self.settings.auth_password_min_length:
            raise ValidationError(
                f"Password minimal {self.settings.auth_password_min_length} karakter."
            )
        existing = await self.repo.get_user_by_email(normalized)
        if existing is not None:
            # Anti-enumeration: sukses generik tanpa membocorkan keberadaan email.
            record_audit(
                self.session,
                actor="anonymous",
                action="auth.register.duplicate",
                result="success",
                correlation_id=correlation_id,
            )
            return

        now = datetime.now(UTC)
        user = User(
            id=new_ulid(),
            normalized_email=normalized,
            email_verified_at=None,
            status="active",
            auth_epoch=0,
        )
        self.repo.add_user(user)
        await self.session.flush()

        credential = PasswordCredential(
            user_id=user.id,
            password_hash=hash_password(self._hasher, password),
            algorithm="argon2id",
            params_version=1,
            changed_at=now,
        )
        self.repo.add_password_credential(credential)
        self.repo.add_profile(UserProfile(user_id=user.id))

        verify_token = self._issue_action_token(user_id=user.id, purpose="email_verify")
        record_outbox_event(
            self.session,
            aggregate_type="user",
            aggregate_id=user.id,
            aggregate_version=0,
            event_type="auth.email_requested.v1",
            payload={"purpose": "email_verify", "email": normalized, "token": verify_token},
        )
        record_audit(
            self.session,
            actor=user.id,
            action="auth.register",
            result="success",
            correlation_id=correlation_id,
        )

    async def verify_email(self, *, token: str) -> None:
        token_row = await self._consume_action_token(token=token, purpose="email_verify")
        user = await self.repo.get_user_by_id(token_row.user_id)
        if user is None:
            raise UnauthorizedError("Token tidak valid.")
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
        record_audit(self.session, actor=user.id, action="auth.email_verified")

    async def resend_verification(self, *, email: str) -> None:
        normalized = normalize_email(email)
        user = await self.repo.get_user_by_email(normalized)
        if user is None or user.email_verified_at is not None:
            return  # generik
        verify_token = self._issue_action_token(user_id=user.id, purpose="email_verify")
        record_outbox_event(
            self.session,
            aggregate_type="user",
            aggregate_id=user.id,
            aggregate_version=0,
            event_type="auth.email_requested.v1",
            payload={"purpose": "email_verify", "email": normalized, "token": verify_token},
        )

    # --- Password reset (FND-06) ---

    async def forgot_password(self, *, email: str) -> None:
        normalized = normalize_email(email)
        user = await self.repo.get_user_by_email(normalized)
        if user is None:
            return  # generik
        reset_token = self._issue_action_token(user_id=user.id, purpose="password_reset")
        record_outbox_event(
            self.session,
            aggregate_type="user",
            aggregate_id=user.id,
            aggregate_version=0,
            event_type="auth.email_requested.v1",
            payload={"purpose": "password_reset", "email": normalized, "token": reset_token},
        )

    async def reset_password(self, *, token: str, new_password: str) -> None:
        if len(new_password) < self.settings.auth_password_min_length:
            raise ValidationError(
                f"Password minimal {self.settings.auth_password_min_length} karakter."
            )
        token_row = await self._consume_action_token(token=token, purpose="password_reset")
        user = await self.repo.get_user_by_id(token_row.user_id)
        if user is None:
            raise UnauthorizedError("Token tidak valid.")

        credential = await self.repo.get_password_credential(user.id)
        now = datetime.now(UTC)
        new_hash = hash_password(self._hasher, new_password)
        if credential is None:
            credential = PasswordCredential(
                user_id=user.id,
                password_hash=new_hash,
                algorithm="argon2id",
                params_version=1,
                changed_at=now,
            )
            self.repo.add_password_credential(credential)
        else:
            credential.password_hash = new_hash
            credential.changed_at = now
            credential.params_version += 1

        # Naikkan epoch dan cabut seluruh session
        user.auth_epoch += 1
        await self._revoke_all_sessions_for_user(user.id)
        record_audit(self.session, actor=user.id, action="auth.password_reset")

    def _issue_action_token(self, *, user_id: str, purpose: str) -> str:
        token = new_opaque_token()
        now = datetime.now(UTC)
        row = AuthActionToken(
            id=new_ulid(),
            user_id=user_id,
            purpose=purpose,
            token_hash=sha256_hex(token),
            expires_at=now + timedelta(seconds=self.settings.auth_action_token_ttl_seconds),
            created_at=now,
        )
        self.repo.add_action_token(row)
        return token

    async def _consume_action_token(self, *, token: str, purpose: str) -> AuthActionToken:
        token_hash = sha256_hex(token)
        row = await self.repo.get_action_token(token_hash, purpose)
        now = datetime.now(UTC)
        if row is None or row.consumed_at is not None or row.expires_at <= now:
            raise UnauthorizedError("Token tidak valid atau kedaluwarsa.")
        row.consumed_at = now
        return row

    # --- Login (FND-07) ---

    async def login(
        self, *, email: str, password: str, correlation_id: str | None = None
    ) -> IssuedSession:
        normalized = normalize_email(email)
        user = await self.repo.get_user_by_email(normalized)
        generic_error = UnauthorizedError("Email atau password salah.")
        if user is None or user.status != "active":
            record_audit(
                self.session,
                actor="anonymous",
                action="auth.login",
                result="failure",
                correlation_id=correlation_id,
            )
            raise generic_error
        credential = await self.repo.get_password_credential(user.id)
        if credential is None or not verify_password(
            self._hasher, credential.password_hash, password
        ):
            record_audit(
                self.session,
                actor=user.id,
                action="auth.login",
                result="failure",
                correlation_id=correlation_id,
            )
            raise generic_error
        if user.email_verified_at is None:
            raise UnauthorizedError("Email belum diverifikasi.", code="EMAIL_NOT_VERIFIED")

        session = self._issue_session(user)
        record_audit(
            self.session,
            actor=user.id,
            action="auth.login",
            result="success",
            correlation_id=correlation_id,
        )
        return session

    def _issue_session(self, user: User) -> IssuedSession:
        from temanbule.platform.security import read_private_key_pem

        now = datetime.now(UTC)
        family_id = new_ulid()
        refresh_token = new_opaque_token()
        session_row = AuthSession(
            id=new_ulid(),
            user_id=user.id,
            family_id=family_id,
            refresh_hash=sha256_hex(refresh_token),
            parent_id=None,
            expires_at=now + timedelta(seconds=self.settings.auth_refresh_token_ttl_seconds),
            auth_epoch=user.auth_epoch,
            created_at=now,
        )
        self.repo.add_session(session_row)

        access_token = issue_access_jwt(
            private_key_pem=read_private_key_pem(self.settings.auth_jwt_private_key_file),
            algorithm=self.settings.auth_jwt_algorithm,
            key_id=self.settings.auth_jwt_key_id,
            issuer=self.settings.auth_jwt_issuer,
            audience=self.settings.auth_jwt_audience,
            subject=user.id,
            family_id=family_id,
            auth_epoch=user.auth_epoch,
            ttl_seconds=self.settings.auth_access_token_ttl_seconds,
        )
        return IssuedSession(
            access_token=access_token,
            refresh_token=refresh_token,
            family_id=family_id,
            user_id=user.id,
        )

    # --- Refresh rotation (FND-07) ---

    async def refresh(self, *, refresh_token: str) -> IssuedSession:
        token_hash = sha256_hex(refresh_token)
        session_row = await self.repo.get_session_by_refresh_hash(token_hash)
        now = datetime.now(UTC)
        if session_row is None:
            raise UnauthorizedError("Refresh token tidak valid.")

        if session_row.revoked_at is not None or session_row.used_at is not None:
            # Reuse detection: cabut seluruh family
            await self._revoke_family(session_row.family_id)
            record_audit(
                self.session,
                actor=session_row.user_id,
                action="auth.refresh.reuse_detected",
                result="denied",
            )
            raise UnauthorizedError("Refresh token sudah dipakai; session dicabut.")
        if session_row.expires_at <= now:
            raise UnauthorizedError("Refresh token kedaluwarsa.")

        user = await self.repo.get_user_by_id(session_row.user_id)
        if user is None or user.status != "active":
            raise UnauthorizedError("Akun tidak aktif.")
        if session_row.auth_epoch != user.auth_epoch:
            raise UnauthorizedError("Session tidak lagi valid.")

        # Rotasi: tandai lama used, terbitkan baru dalam family yang sama
        session_row.used_at = now
        new_refresh = new_opaque_token()
        new_row = AuthSession(
            id=new_ulid(),
            user_id=user.id,
            family_id=session_row.family_id,
            refresh_hash=sha256_hex(new_refresh),
            parent_id=session_row.id,
            expires_at=now + timedelta(seconds=self.settings.auth_refresh_token_ttl_seconds),
            auth_epoch=user.auth_epoch,
            created_at=now,
        )
        self.repo.add_session(new_row)

        from temanbule.platform.security import read_private_key_pem

        access_token = issue_access_jwt(
            private_key_pem=read_private_key_pem(self.settings.auth_jwt_private_key_file),
            algorithm=self.settings.auth_jwt_algorithm,
            key_id=self.settings.auth_jwt_key_id,
            issuer=self.settings.auth_jwt_issuer,
            audience=self.settings.auth_jwt_audience,
            subject=user.id,
            family_id=session_row.family_id,
            auth_epoch=user.auth_epoch,
            ttl_seconds=self.settings.auth_access_token_ttl_seconds,
        )
        return IssuedSession(
            access_token=access_token,
            refresh_token=new_refresh,
            family_id=session_row.family_id,
            user_id=user.id,
        )

    async def logout(self, *, refresh_token: str) -> None:
        token_hash = sha256_hex(refresh_token)
        session_row = await self.repo.get_session_by_refresh_hash(token_hash)
        if session_row is None:
            return
        await self._revoke_family(session_row.family_id)
        record_audit(self.session, actor=session_row.user_id, action="auth.logout")

    async def logout_all(self, *, user_id: str) -> None:
        user = await self.repo.get_user_by_id(user_id)
        if user is None:
            return
        user.auth_epoch += 1
        await self._revoke_all_sessions_for_user(user_id)
        record_audit(self.session, actor=user_id, action="auth.logout_all")

    async def _revoke_family(self, family_id: str) -> None:
        now = datetime.now(UTC)
        rows = await self.repo.get_active_family_sessions(family_id)
        for row in rows:
            row.revoked_at = now

    async def _revoke_all_sessions_for_user(self, user_id: str) -> None:
        from sqlalchemy import select

        from temanbule.modules.identity.models import AuthSession as _AS

        now = datetime.now(UTC)
        stmt = select(_AS).where(_AS.user_id == user_id, _AS.revoked_at.is_(None))
        result = await self.session.execute(stmt)
        for row in result.scalars().all():
            row.revoked_at = now

    # --- Access token validation (session family + epoch) ---

    async def validate_access_token_claims(
        self, *, user_id: str, family_id: str, auth_epoch: int
    ) -> User:
        user = await self.repo.get_user_by_id(user_id)
        if user is None or user.status != "active":
            raise UnauthorizedError("Akun tidak aktif.")
        if user.auth_epoch != auth_epoch:
            raise UnauthorizedError("Token tidak lagi valid.")
        sessions = await self.repo.get_active_family_sessions(family_id)
        if not sessions:
            raise UnauthorizedError("Session telah dicabut.")
        return user

    # --- Google OIDC (FND-08) ---

    async def start_google_flow(
        self, *, intent: str, bound_user_id: str | None, browser_fingerprint: str
    ) -> tuple[str, str]:
        """Buat oauth transaction; return (state, authorization_url_params)."""
        state = new_opaque_token()
        nonce = new_opaque_token()
        code_verifier = base64url_no_pad(secrets.token_bytes(64))
        code_challenge = base64url_no_pad(
            hashlib.sha256(code_verifier.encode("ascii")).digest()
        )
        now = datetime.now(UTC)
        txn = OauthTransaction(
            id=new_ulid(),
            state_hash=sha256_hex(state),
            nonce_hash=sha256_hex(nonce),
            encrypted_pkce_verifier=code_verifier,  # envelope encryption menyusul (DEC-02)
            intent=intent,
            bound_user_id=bound_user_id,
            browser_binding_hash=sha256_hex(browser_fingerprint),
            expires_at=now + timedelta(seconds=self.settings.auth_oauth_transaction_ttl_seconds),
            created_at=now,
        )
        self.repo.add_oauth_transaction(txn)
        params = (
            f"client_id={self.settings.google_client_id}"
            f"&redirect_uri={self.settings.google_redirect_uri}"
            "&response_type=code"
            "&scope=openid%20email%20profile"
            f"&state={state}"
            f"&nonce={nonce}"
            f"&code_challenge={code_challenge}"
            "&code_challenge_method=S256"
        )
        return state, params

    async def consume_google_transaction(self, *, state: str) -> OauthTransaction:
        state_hash = sha256_hex(state)
        txn = await self.repo.get_oauth_transaction_by_state_hash(state_hash)
        now = datetime.now(UTC)
        if txn is None or txn.consumed_at is not None or txn.expires_at <= now:
            raise UnauthorizedError("State OAuth tidak valid atau kedaluwarsa.")
        txn.consumed_at = now
        return txn

    async def complete_google_login(
        self, *, txn: OauthTransaction, google_subject: str, google_email: str | None
    ) -> IssuedSession:
        """Login via Google; tidak auto-link hanya karena email sama."""
        identity = await self.repo.get_identity("google", google_subject)
        if identity is None:
            # User baru Google: buat account verified
            if not google_email:
                raise UnauthorizedError("Email Google diperlukan untuk registrasi.")
            normalized = normalize_email(google_email)
            existing = await self.repo.get_user_by_email(normalized)
            if existing is not None:
                # Email sudah ada: jangan auto-link; minta login + explicit link
                raise ConflictError(
                    "Email sudah terdaftar. Login lalu tautkan Google dari Profile.",
                    code="GOOGLE_LINK_REQUIRED",
                )
            now = datetime.now(UTC)
            user = User(
                id=new_ulid(),
                normalized_email=normalized,
                email_verified_at=now,
                status="active",
                auth_epoch=0,
            )
            self.repo.add_user(user)
            await self.session.flush()
            self.repo.add_profile(UserProfile(user_id=user.id))
            self.repo.add_identity(
                AuthIdentity(
                    id=new_ulid(), user_id=user.id, provider="google", subject=google_subject
                )
            )
            record_audit(self.session, actor=user.id, action="auth.google.register")
            return self._issue_session(user)

        existing_user = await self.repo.get_user_by_id(identity.user_id)
        if existing_user is None or existing_user.status != "active":
            raise UnauthorizedError("Akun tidak aktif.")
        record_audit(self.session, actor=existing_user.id, action="auth.google.login")
        return self._issue_session(existing_user)

    async def link_google(
        self, *, user_id: str, google_subject: str
    ) -> None:
        """Explicit link setelah reauth; tolak bila subject sudah terikat akun lain."""
        existing = await self.repo.get_identity("google", google_subject)
        if existing is not None:
            if existing.user_id == user_id:
                return  # sudah ter-link
            raise ConflictError("Identitas Google sudah terhubung ke akun lain.")
        self.repo.add_identity(
            AuthIdentity(id=new_ulid(), user_id=user_id, provider="google", subject=google_subject)
        )
        record_audit(self.session, actor=user_id, action="auth.google.link")

    async def unlink_google(self, *, user_id: str) -> None:
        """Unlink tidak boleh menghapus satu-satunya metode login."""
        identities = await self.repo.get_identities_for_user(user_id)
        google_identity = next((i for i in identities if i.provider == "google"), None)
        if google_identity is None:
            return
        has_password = await self.repo.get_password_credential(user_id) is not None
        other_identities = [i for i in identities if i.provider != "google"]
        if not has_password and not other_identities:
            raise ConflictError(
                "Tidak dapat melepas satu-satunya metode login.",
                code="LAST_AUTH_METHOD",
            )
        await self.repo.delete_identity(google_identity)
        record_audit(self.session, actor=user_id, action="auth.google.unlink")
