"""Adapter konkret malware scanner (DEC-15): HTTP microservice scan.

Kontrak:
- ``scan(storage_key, media_type) -> str`` mengembalikan ``clean`` |
  ``malicious`` | ``error``.
- Microservice eksternal di ``MEDIA_SCAN_SERVICE_URL`` menerima POST JSON
  ``{"storage_key", "media_type"}`` dengan header bearer
  ``MEDIA_SCAN_SERVICE_TOKEN`` dan membalas ``{"scan_state": "..."}``.
- Kegagalan jaringan/HTTP/validasi diperlakukan sebagai ``error`` (bukan
  clean), agar media tidak pernah otomatis dianggap aman saat scanner down.
- Token tidak pernah masuk log/error.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ValidationError

from temanbule.platform.settings import Settings

_SCAN_TIMEOUT_SECONDS = 15.0


class _ScanResponse(BaseModel):
    scan_state: str


class HttpMalwareScanner:
    """Scanner via microservice HTTP; gagal → 'error' (fail-closed)."""

    def __init__(self, settings: Settings) -> None:
        self._url = settings.media_scan_service_url
        self._token = settings.media_scan_service_token

    async def scan(self, *, storage_key: str, media_type: str) -> str:
        if not self._url or not self._token:
            # Belum dikonfigurasi: fail-closed, jangan anggap clean.
            return "error"
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_SCAN_TIMEOUT_SECONDS),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"storage_key": storage_key, "media_type": media_type},
                )
                response.raise_for_status()
                parsed = _ScanResponse.model_validate(response.json())
        except (httpx.HTTPError, ValidationError, ValueError):
            return "error"
        if parsed.scan_state in ("clean", "malicious"):
            return parsed.scan_state
        return "error"


def build_malware_scanner(settings: Settings) -> HttpMalwareScanner:
    """Bangun scanner konkret untuk disuntikkan ke MediaService."""
    return HttpMalwareScanner(settings)
