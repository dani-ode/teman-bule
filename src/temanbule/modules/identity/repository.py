"""Identity repository: akses data users/credentials/sessions/tokens/identities."""

from __future__ import annotations

from sqlalchemy import select
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


class IdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- Users ---
    async def get_user_by_email(self, normalized_email: str) -> User | None:
        stmt = select(User).where(User.normalized_email == normalized_email)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: str) -> User | None:
        return await self.session.get(User, user_id)

    def add_user(self, user: User) -> None:
        self.session.add(user)

    # --- Profile ---
    def add_profile(self, profile: UserProfile) -> None:
        self.session.add(profile)

    async def get_profile(self, user_id: str) -> UserProfile | None:
        return await self.session.get(UserProfile, user_id)

    # --- Password credentials ---
    async def get_password_credential(self, user_id: str) -> PasswordCredential | None:
        return await self.session.get(PasswordCredential, user_id)

    def add_password_credential(self, credential: PasswordCredential) -> None:
        self.session.add(credential)

    # --- Sessions ---
    def add_session(self, session_row: AuthSession) -> None:
        self.session.add(session_row)

    async def get_session_by_refresh_hash(self, refresh_hash: str) -> AuthSession | None:
        stmt = select(AuthSession).where(AuthSession.refresh_hash == refresh_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_family_sessions(self, family_id: str) -> list[AuthSession]:
        stmt = select(AuthSession).where(
            AuthSession.family_id == family_id,
            AuthSession.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_sessions_for_user(
        self, user_id: str, auth_epoch: int
    ) -> list[AuthSession]:
        stmt = select(AuthSession).where(
            AuthSession.user_id == user_id,
            AuthSession.revoked_at.is_(None),
            AuthSession.auth_epoch == auth_epoch,
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # --- Action tokens ---
    def add_action_token(self, token: AuthActionToken) -> None:
        self.session.add(token)

    async def get_action_token(self, token_hash: str, purpose: str) -> AuthActionToken | None:
        stmt = select(AuthActionToken).where(
            AuthActionToken.token_hash == token_hash,
            AuthActionToken.purpose == purpose,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # --- OAuth transactions ---
    def add_oauth_transaction(self, txn: OauthTransaction) -> None:
        self.session.add(txn)

    async def get_oauth_transaction_by_state_hash(self, state_hash: str) -> OauthTransaction | None:
        stmt = select(OauthTransaction).where(OauthTransaction.state_hash == state_hash)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # --- Auth identities ---
    async def get_identity(self, provider: str, subject: str) -> AuthIdentity | None:
        stmt = select(AuthIdentity).where(
            AuthIdentity.provider == provider,
            AuthIdentity.subject == subject,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_identities_for_user(self, user_id: str) -> list[AuthIdentity]:
        stmt = select(AuthIdentity).where(AuthIdentity.user_id == user_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def add_identity(self, identity: AuthIdentity) -> None:
        self.session.add(identity)

    async def delete_identity(self, identity: AuthIdentity) -> None:
        await self.session.delete(identity)
