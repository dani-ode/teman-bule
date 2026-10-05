"""HTTP client tipis dari realtime worker ke internal calls API (FND-09).

Worker tidak menulis DB domain secara langsung: state lifecycle call
(admit/activate/turns/end) dimiliki API otoritatif. Client ini membawa M2M
service token pada header terkonfigurasi; token tidak pernah masuk log atau
pesan error. Base URL wajib dari settings — tanpa default tertanam.
"""

from __future__ import annotations

from typing import Any

import httpx

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings


class InternalApiError(DependencyUnavailableError):
    """Kegagalan panggilan internal API; code stabil tanpa body server."""

    code = "INTERNAL_API_CALL_FAILED"


class RealtimeApiClient:
    """Client M2M untuk ``/internal/v1/calls`` dengan service token realtime."""

    def __init__(self, settings: Settings) -> None:
        base_url = settings.app_internal_base_url.strip() or settings.app_public_url.strip()
        if not base_url:
            raise ValueError(
                "APP_INTERNAL_BASE_URL atau APP_PUBLIC_URL wajib diisi untuk realtime worker"
            )
        if not settings.m2m_realtime_service_token.strip():
            raise ValueError("M2M_REALTIME_SERVICE_TOKEN wajib untuk realtime worker")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={settings.m2m_token_header: settings.m2m_realtime_service_token},
            timeout=httpx.Timeout(15.0, connect=5.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise InternalApiError(
                "Gagal menghubungi internal calls API.",
            ) from exc
        if response.status_code >= 400:
            raise InternalApiError(
                "Internal calls API menolak request.",
                code=f"INTERNAL_API_{response.status_code}",
            )
        return response

    async def get_session_config(self, session_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/internal/v1/calls/{session_id}/config")
        return response.json()  # type: ignore[no-any-return]

    async def admit(
        self, session_id: str, *, lease_owner: str, lease_seconds: int
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/internal/v1/calls/{session_id}:admit",
            json={"lease_owner": lease_owner, "lease_seconds": lease_seconds},
        )
        return response.json()  # type: ignore[no-any-return]

    async def activate(
        self, session_id: str, *, fencing_token: int, lease_owner: str
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/internal/v1/calls/{session_id}:activate",
            json={"fencing_token": fencing_token, "lease_owner": lease_owner},
        )
        return response.json()  # type: ignore[no-any-return]

    async def record_turn(
        self,
        session_id: str,
        *,
        fencing_token: int,
        lease_owner: str,
        speaker: str,
        epoch: int,
        text: str | None,
        interrupted: bool,
    ) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/internal/v1/calls/{session_id}/turns",
            json={
                "fencing_token": fencing_token,
                "lease_owner": lease_owner,
                "speaker": speaker,
                "epoch": epoch,
                "text": text,
                "interrupted": interrupted,
            },
        )
        return response.json()  # type: ignore[no-any-return]

    async def end(self, session_id: str, *, end_reason: str) -> dict[str, Any]:
        response = await self._request(
            "POST",
            f"/internal/v1/calls/{session_id}:end",
            json={"end_reason": end_reason},
        )
        return response.json()  # type: ignore[no-any-return]
