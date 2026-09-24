"""Google OIDC router (FND-08)."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from temanbule.api.deps import CurrentUser, SessionDep, SettingsDep
from temanbule.api.schemas.auth import GoogleStartResponse
from temanbule.modules.identity.auth_service import AuthService
from temanbule.platform.errors import FeatureUnavailableError, UnauthorizedError
from temanbule.platform.oidc import GoogleOidcClient
from temanbule.platform.settings import Settings

router = APIRouter(prefix="/v1/auth/google", tags=["google-auth"])


def _require_google_enabled(settings: Settings) -> None:
    if not settings.feature_google_auth_enabled:
        raise FeatureUnavailableError("Google auth tidak aktif.")


def _make_oidc_client(settings: Settings) -> GoogleOidcClient:
    return GoogleOidcClient(
        discovery_url=settings.google_discovery_url,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        jwks_cache_seconds=settings.auth_jwks_cache_seconds,
    )


def _browser_fingerprint(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    return f"{request.client.host if request.client else ''}|{ua}"


def _authorization_url(params: str) -> str:
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


@router.get("/start", response_model=GoogleStartResponse)
async def google_start(
    request: Request, session: SessionDep, settings: SettingsDep
) -> GoogleStartResponse:
    _require_google_enabled(settings)
    auth = AuthService(session, settings)
    async with session.begin():
        _state, params = await auth.start_google_flow(
            intent="login",
            bound_user_id=None,
            browser_fingerprint=_browser_fingerprint(request),
        )
    return GoogleStartResponse(authorization_url=_authorization_url(params))


@router.get("/callback")
async def google_callback(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    _require_google_enabled(settings)
    if error is not None or not code or not state:
        return RedirectResponse(url=f"{settings.auth_frontend_error_url}?reason=oauth_failed")

    auth = AuthService(session, settings)
    oidc = _make_oidc_client(settings)
    async with session.begin():
        txn = await auth.consume_google_transaction(state=state)
        id_token = await oidc.exchange_code(
            code=code,
            redirect_uri=settings.google_redirect_uri,
            code_verifier=txn.encrypted_pkce_verifier,
        )
        # Verifikasi signature/iss/aud/exp, lalu bandingkan hash nonce terhadap
        # nonce_hash tersimpan (nonce asli tidak disimpan di DB).
        claims = await oidc.verify_id_token_claims(id_token=id_token)
        nonce = str(claims.get("nonce", ""))
        if hashlib.sha256(nonce.encode()).hexdigest() != txn.nonce_hash:
            raise UnauthorizedError("Nonce Google tidak cocok.")

        if txn.intent == "link" and txn.bound_user_id:
            await auth.link_google(user_id=txn.bound_user_id, google_subject=claims["sub"])
            return RedirectResponse(
                url=f"{settings.auth_frontend_success_url}?linked=google"
            )

        issued = await auth.complete_google_login(
            txn=txn,
            google_subject=claims["sub"],
            google_email=claims.get("email"),
        )
        redirect = RedirectResponse(url=settings.auth_frontend_success_url)
        redirect.set_cookie(
            key="refresh_token",
            value=issued.refresh_token,
            max_age=settings.auth_refresh_token_ttl_seconds,
            httponly=True,
            secure=settings.auth_cookie_secure,
            samesite=settings.auth_cookie_samesite,  # type: ignore[arg-type]
            domain=settings.auth_cookie_domain or None,
            path="/v1/auth",
        )
        # Frontend memperoleh access token melalui /refresh setelah redirect.
        return redirect


# --- Link/unlink endpoints ---


@router.post("/me/identities/google:link", response_model=GoogleStartResponse)
async def google_link_start(
    request: Request,
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> GoogleStartResponse:
    _require_google_enabled(settings)
    auth = AuthService(session, settings)
    async with session.begin():
        _state, params = await auth.start_google_flow(
            intent="link",
            bound_user_id=current_user.id,
            browser_fingerprint=_browser_fingerprint(request),
        )
    return GoogleStartResponse(authorization_url=_authorization_url(params))


@router.delete("/me/identities/google", status_code=204)
async def google_unlink(
    current_user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
) -> None:
    _require_google_enabled(settings)
    auth = AuthService(session, settings)
    async with session.begin():
        await auth.unlink_google(user_id=current_user.id)
