"""Google OIDC client: discovery, JWKS cache, ID token verification (FND-08)."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

from temanbule.platform.errors import DependencyUnavailableError, UnauthorizedError


class GoogleOidcClient:
    """Verifikasi ID token Google; tidak menyimpan token provider."""

    def __init__(
        self,
        *,
        discovery_url: str,
        client_id: str,
        client_secret: str,
        jwks_cache_seconds: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._discovery_url = discovery_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._jwks_cache_seconds = jwks_cache_seconds
        self._http = http_client or httpx.AsyncClient(timeout=10.0)
        self._discovery: dict[str, Any] | None = None
        self._jwks: dict[str, Any] | None = None
        self._jwks_fetched_at: float = 0.0

    async def _get_discovery(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        try:
            resp = await self._http.get(self._discovery_url)
            resp.raise_for_status()
            self._discovery = resp.json()
            return self._discovery
        except (httpx.HTTPError, ValueError) as exc:
            raise DependencyUnavailableError(
                "Google discovery tidak tersedia.", code="OIDC_DISCOVERY_UNAVAILABLE"
            ) from exc

    async def _get_jwks(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._jwks is not None and (now - self._jwks_fetched_at) < self._jwks_cache_seconds:
            return self._jwks
        discovery = await self._get_discovery()
        jwks_uri = discovery["jwks_uri"]
        try:
            resp = await self._http.get(jwks_uri)
            resp.raise_for_status()
            self._jwks = resp.json()
            self._jwks_fetched_at = now
            return self._jwks
        except (httpx.HTTPError, ValueError) as exc:
            raise DependencyUnavailableError(
                "Google JWKS tidak tersedia.", code="OIDC_JWKS_UNAVAILABLE"
            ) from exc

    async def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        """Tukar authorization code menjadi id_token (server-side)."""
        discovery = await self._get_discovery()
        token_endpoint = discovery["token_endpoint"]
        try:
            resp = await self._http.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "code_verifier": code_verifier,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            return str(payload["id_token"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise UnauthorizedError("Penukaran kode Google gagal.") from exc

    async def verify_id_token_claims(self, *, id_token: str) -> dict[str, Any]:
        """Verifikasi signature/JWKS, issuer, audience, expiry, sub, email_verified.

        Nonce diverifikasi terpisah oleh pemanggil (hash comparison), karena
        backend hanya menyimpan hash nonce, bukan nilai aslinya.
        """
        jwks = await self._get_jwks()
        try:
            unverified_header = pyjwt.get_unverified_header(id_token)
            kid = unverified_header["kid"]
            key_data = next(k for k in jwks["keys"] if k["kid"] == kid)
            public_key = RSAAlgorithm.from_jwk(key_data)
            claims: dict[str, Any] = pyjwt.decode(
                id_token,
                public_key,  # type: ignore[arg-type]
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=["https://accounts.google.com", "accounts.google.com"],
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except (pyjwt.PyJWTError, KeyError, StopIteration) as exc:
            raise UnauthorizedError("ID token Google tidak valid.") from exc

        if not claims.get("email_verified"):
            raise UnauthorizedError("Email Google belum terverifikasi.")
        if not claims.get("sub"):
            raise UnauthorizedError("Subject Google tidak ada.")
        return claims
