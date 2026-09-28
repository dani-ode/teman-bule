"""Adapter konkret verifikasi credential BYOK (DEC-08).

Mengimplementasikan ``CredentialVerificationPort`` dari
``temanbule.modules.catalog.byok`` dengan panggilan ringan nyata ke provider:

- Gemini (``provider_catalog.code == "gemini"``): ``GET {base}/v1beta/models``
  dengan header ``x-goog-api-key``.
- OpenAI (``provider_catalog.code == "openai"``): ``GET {base}/models`` dengan
  header ``Authorization: Bearer``.

Kontrak kegagalan:
- Penolakan autentikasi/otorisasi provider (HTTP 401/403) → credential invalid,
  return ``False`` (bukan exception).
- Kegagalan transport, timeout, status tak terduga, atau provider tidak
  didukung → ``DependencyUnavailableError`` agar tidak ada credential yang
  dianggap valid/invalid tanpa bukti otoritatif.

API key milik user tidak pernah ditulis ke log maupun pesan error; custom
``base_url`` hanya diterima bila memakai skema HTTPS (service BYOK sudah
memfilter ``allows_custom_base_url`` sebelum memanggil port ini).
"""

from __future__ import annotations

import httpx

from temanbule.platform.errors import DependencyUnavailableError
from temanbule.platform.settings import Settings

_PROVIDER_GEMINI = "gemini"
_PROVIDER_OPENAI = "openai"

_GEMINI_MODELS_PATH = "/v1beta/models"
_OPENAI_MODELS_PATH = "/models"

_VERIFY_TIMEOUT_SECONDS = 10.0


class ProviderCredentialVerifier:
    """Implementasi nyata ``CredentialVerificationPort`` via HTTP provider."""

    def __init__(
        self,
        *,
        gemini_base_url: str,
        openai_base_url: str,
        timeout_seconds: float = _VERIFY_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_urls = {
            _PROVIDER_GEMINI: gemini_base_url.rstrip("/"),
            _PROVIDER_OPENAI: openai_base_url.rstrip("/"),
        }
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport

    async def verify(self, *, provider_code: str, api_key: str, base_url: str | None) -> bool:
        """Verifikasi credential ke provider; True hanya bila provider menerima."""
        normalized = provider_code.strip().lower()
        if normalized not in self._base_urls:
            raise DependencyUnavailableError(
                "Provider credential tidak didukung verifier.",
                code="BYOK_PROVIDER_UNSUPPORTED",
            )
        if not api_key.strip():
            return False

        effective_base = self._resolve_base_url(normalized, base_url)
        if normalized == _PROVIDER_GEMINI:
            url = effective_base + _GEMINI_MODELS_PATH
            headers = {"x-goog-api-key": api_key, "Accept": "application/json"}
        else:
            url = effective_base + _OPENAI_MODELS_PATH
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            }
        return await self._probe(url=url, headers=headers)

    def _resolve_base_url(self, provider_code: str, base_url: str | None) -> str:
        """Pilih base URL; custom wajib HTTPS agar credential tidak bocor."""
        if base_url is None or not base_url.strip():
            return self._base_urls[provider_code]
        candidate = base_url.strip().rstrip("/")
        if not candidate.startswith("https://"):
            raise DependencyUnavailableError(
                "Custom base_url provider wajib memakai HTTPS.",
                code="BYOK_BASE_URL_REJECTED",
            )
        return candidate

    async def _probe(self, *, url: str, headers: dict[str, str]) -> bool:
        """Panggilan ringan daftar model; petakan hasil ke kontrak port."""
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "Transport ke provider credential gagal.",
                code="BYOK_PROVIDER_TRANSPORT_FAILED",
            ) from exc

        if response.status_code in (401, 403):
            return False
        if 200 <= response.status_code < 300:
            return True
        raise DependencyUnavailableError(
            f"Provider credential menjawab tak terduga (HTTP {response.status_code}).",
            code="BYOK_PROVIDER_HTTP_ERROR",
        )


def build_credential_verifier(settings: Settings) -> ProviderCredentialVerifier:
    """Bangun verifier terkonfigurasi dari settings backend yang tepercaya.

    Raise ValueError bila base URL default provider kosong (tanpa menyebut
    nilai); Settings sudah menyediakan default produksi.
    """
    required = {
        "gemini_base_url": settings.gemini_base_url,
        "openai_base_url": settings.openai_base_url,
    }
    missing = [
        name for name, value in required.items() if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError(
            "Konfigurasi verifier credential tidak lengkap: " + ", ".join(sorted(missing))
        )
    return ProviderCredentialVerifier(
        gemini_base_url=settings.gemini_base_url,
        openai_base_url=settings.openai_base_url,
    )
