"""Auth router (FND-06/07/08)."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.api.schemas.auth import (
    ForgotPasswordRequest,
    GenericAcceptedResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResendRequest,
    ResetPasswordRequest,
    TokenPairResponse,
    TokenRequest,
)
from temanbule.modules.identity.auth_service import AuthService
from temanbule.platform.errors import UnauthorizedError
from temanbule.platform.logging import get_request_id
from temanbule.platform.settings import Settings

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, settings: Settings, refresh_token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        max_age=settings.auth_refresh_token_ttl_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,  # type: ignore[arg-type]
        domain=settings.auth_cookie_domain or None,
        path="/v1/auth",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key="refresh_token",
        domain=settings.auth_cookie_domain or None,
        path="/v1/auth",
    )


@router.post("/register", status_code=202, response_model=GenericAcceptedResponse)
async def register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.register(
            email=str(body.email), password=body.password, correlation_id=get_request_id()
        )
    return GenericAcceptedResponse()


@router.post("/email:verify", status_code=200, response_model=GenericAcceptedResponse)
async def verify_email(
    body: TokenRequest, session: SessionDep, settings: SettingsDep
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.verify_email(token=body.token)
    return GenericAcceptedResponse(status="verified")


@router.post("/email:resend", status_code=202, response_model=GenericAcceptedResponse)
async def resend_verification(
    body: ResendRequest, session: SessionDep, settings: SettingsDep
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.resend_verification(email=str(body.email))
    return GenericAcceptedResponse()


@router.post("/password:forgot", status_code=202, response_model=GenericAcceptedResponse)
async def forgot_password(
    body: ForgotPasswordRequest, session: SessionDep, settings: SettingsDep
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.forgot_password(email=str(body.email))
    return GenericAcceptedResponse()


@router.post("/password:reset", status_code=200, response_model=GenericAcceptedResponse)
async def reset_password(
    body: ResetPasswordRequest, session: SessionDep, settings: SettingsDep
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.reset_password(token=body.token, new_password=body.new_password)
    return GenericAcceptedResponse(status="reset")


@router.post("/login", response_model=TokenPairResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPairResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        issued = await auth.login(
            email=str(body.email), password=body.password, correlation_id=get_request_id()
        )
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return TokenPairResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=settings.auth_access_token_ttl_seconds,
    )


@router.post("/refresh", response_model=TokenPairResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenPairResponse:
    token = body.refresh_token or request.cookies.get("refresh_token")
    if not token:
        raise UnauthorizedError("Refresh token diperlukan.")
    auth = AuthService(session, settings)
    async with session.begin():
        issued = await auth.refresh(refresh_token=token)
    _set_refresh_cookie(response, settings, issued.refresh_token)
    return TokenPairResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_in=settings.auth_access_token_ttl_seconds,
    )


@router.post("/logout", status_code=200, response_model=GenericAcceptedResponse)
async def logout(
    body: RefreshRequest,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> GenericAcceptedResponse:
    token = body.refresh_token or request.cookies.get("refresh_token")
    if token:
        auth = AuthService(session, settings)
        async with session.begin():
            await auth.logout(refresh_token=token)
    _clear_refresh_cookie(response, settings)
    return GenericAcceptedResponse(status="logged_out")


@router.post("/logout-all", status_code=200, response_model=GenericAcceptedResponse)
async def logout_all(
    current_user: CurrentUser,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> GenericAcceptedResponse:
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.logout_all(user_id=current_user.id)
    _clear_refresh_cookie(response, settings)
    return GenericAcceptedResponse(status="logged_out_all")
