"""API dependencies: settings, DB session, current principal, service auth."""

from __future__ import annotations

import hmac
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from temanbule.modules.identity.auth_service import AuthService
from temanbule.modules.identity.models import User
from temanbule.platform.errors import ForbiddenError, UnauthorizedError
from temanbule.platform.security import read_public_key_pem, verify_access_jwt
from temanbule.platform.settings import Settings


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if authorization is None or not authorization.startswith("Bearer "):
        raise UnauthorizedError("Token akses diperlukan.")
    token = authorization.removeprefix("Bearer ").strip()
    claims = verify_access_jwt(
        public_key_pem=read_public_key_pem(settings.auth_jwt_public_key_file),
        algorithm=settings.auth_jwt_algorithm,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        token=token,
        clock_skew_seconds=settings.auth_clock_skew_seconds,
    )
    auth = AuthService(session, settings)
    return await auth.validate_access_token_claims(
        user_id=claims.subject,
        family_id=claims.family_id,
        auth_epoch=claims.auth_epoch,
    )


CurrentUser = Annotated[User, Depends(get_current_user)]


# --- Service-to-service auth (FND-09) ---

_SERVICE_TOKEN_ATTR = {
    "langflow": "m2m_langflow_service_token",
    "callcraft": "m2m_callcraft_service_token",
    "realtime": "m2m_realtime_service_token",
}


async def require_service_identity(
    request: Request,
    settings: SettingsDep,
    x_service_token: Annotated[str | None, Header()] = None,
) -> str:
    """Validasi M2M service token; return service identity. Constant-time compare."""
    if x_service_token is None:
        raise UnauthorizedError("Service token diperlukan.")
    for identity, attr in _SERVICE_TOKEN_ATTR.items():
        expected: str = getattr(settings, attr, "")
        if expected and hmac.compare_digest(x_service_token, expected):
            return identity
    raise ForbiddenError("Service token tidak valid.")
