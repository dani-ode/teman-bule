"""Adapter konkret Xendit Checkout (Invoice API) untuk billing TemanBule.

Mengimplementasikan ``XenditCheckoutPort`` memakai Xendit Invoice API v2
(XENDIT_PAYMENT_PRODUCT=INVOICE). Seluruh kegagalan transport/HTTP dipetakan
ke ``DependencyUnavailableError`` dengan pesan aman — tidak ada mock, tidak ada
fallback sukses palsu, dan secret/response body mentah tidak pernah bocor ke
log maupun error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from temanbule.modules.billing.payments import CheckoutResult
from temanbule.platform.errors import DependencyUnavailableError

_INVOICES_PATH = "/v2/invoices"

_ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {
        "BIF",
        "CLP",
        "DJF",
        "GNF",
        "IDR",
        "JPY",
        "KMF",
        "KRW",
        "MGA",
        "PYG",
        "RWF",
        "UGX",
        "VND",
        "VUV",
        "XAF",
        "XOF",
        "XPF",
    }
)
"""Mata uang tanpa fractional unit (ISO 4217 exponent 0); 1 major = 1 minor."""


@dataclass(frozen=True)
class XenditAdapterConfig:
    """Konfigurasi tepercaya untuk adapter; dibangun hanya dari settings backend."""

    base_url: str
    secret_key: str
    payment_product: str
    api_version: str
    environment: str
    success_redirect_url: str
    failure_redirect_url: str
    timeout_seconds: float = 30.0

    def invoices_endpoint(self) -> str:
        return self.base_url.rstrip("/") + _INVOICES_PATH


def _minor_to_major(*, amount_minor: int, currency: str) -> int:
    """Konversi minor unit ke major unit dengan integer arithmetic (tanpa float)."""
    if amount_minor <= 0:
        raise ValueError("amount_minor harus bilangan bulat positif")
    if currency.upper() in _ZERO_DECIMAL_CURRENCIES:
        return amount_minor
    major, remainder = divmod(amount_minor, 100)
    if remainder != 0:
        raise ValueError(
            "amount_minor tidak dapat direpresentasikan dalam major unit"
            f" untuk currency {currency!r} (sisa {remainder})"
        )
    return major


def _parse_expiry(value: Any) -> datetime | None:
    """Parse ISO-8601 expiry_date Xendit; None bila tidak ada/tidak valid."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _extract_string(body: dict[str, Any], field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DependencyUnavailableError(
            "Respons Xendit tidak memuat field yang diharapkan.",
            code="XENDIT_INVALID_RESPONSE",
        )
    return value


class XenditCheckoutAdapter:
    """Implementasi nyata ``XenditCheckoutPort`` via Xendit Invoice API."""

    def __init__(
        self,
        config: XenditAdapterConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not config.base_url.startswith("https://"):
            raise ValueError("Xendit base URL harus memakai HTTPS")
        if config.payment_product.strip().upper() != "INVOICE":
            raise ValueError(
                "XenditCheckoutAdapter hanya mendukung XENDIT_PAYMENT_PRODUCT=INVOICE"
            )
        self.config = config
        self.transport = transport

    async def create_checkout(
        self,
        *,
        merchant_reference: str,
        amount_minor: int,
        currency: str,
        description: str,
        expires_at: datetime | None,
    ) -> CheckoutResult:
        """Buat invoice Xendit; map id/invoice_url/expiry_date ke CheckoutResult."""
        amount_major = _minor_to_major(amount_minor=amount_minor, currency=currency)
        payload: dict[str, Any] = {
            "external_id": merchant_reference,
            "amount": amount_major,
            "currency": currency.upper(),
            "description": description,
        }
        if self.config.success_redirect_url.strip():
            payload["success_redirect_url"] = self.config.success_redirect_url
        if self.config.failure_redirect_url.strip():
            payload["failure_redirect_url"] = self.config.failure_redirect_url
        if expires_at is not None:
            now = datetime.now(UTC)
            expiry = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
            duration = int((expiry - now).total_seconds())
            if duration <= 0:
                raise ValueError("expires_at harus di masa depan")
            payload["invoice_duration"] = duration

        body = await self._request(
            "POST", self.config.invoices_endpoint(), json_body=payload
        )
        return CheckoutResult(
            provider_payment_id=_extract_string(body, "id"),
            checkout_url=_extract_string(body, "invoice_url"),
            expires_at=_parse_expiry(body.get("expiry_date")),
        )

    async def lookup_payment(self, *, provider_payment_id: str) -> str:
        """Ambil status otoritatif invoice (PENDING/PAID/EXPIRED/SETTLED)."""
        endpoint = self.config.invoices_endpoint() + "/" + provider_payment_id
        body = await self._request("GET", endpoint, json_body=None)
        return _extract_string(body, "status").upper()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Eksekusi HTTP request dengan auth Basic; petakan seluruh kegagalan
        ke DependencyUnavailableError tanpa membocorkan secret/body mentah."""
        try:
            async with httpx.AsyncClient(
                auth=(self.config.secret_key, ""),
                timeout=float(self.config.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    url,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                        "x-api-version": self.config.api_version,
                    },
                    json=json_body,
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise DependencyUnavailableError(
                "Hasil request Xendit tidak diketahui; rekonsiliasi sebelum retry.",
                code="XENDIT_OUTCOME_UNKNOWN",
            ) from exc
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                "Transport ke Xendit gagal.", code="XENDIT_TRANSPORT_FAILED"
            ) from exc

        if response.status_code >= 400:
            raise DependencyUnavailableError(
                f"Xendit menolak request (HTTP {response.status_code}).",
                code="XENDIT_HTTP_ERROR",
            )

        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DependencyUnavailableError(
                "Xendit mengembalikan JSON tidak valid.",
                code="XENDIT_INVALID_RESPONSE",
            ) from exc
        if not isinstance(body, dict):
            raise DependencyUnavailableError(
                "Envelope respons Xendit tidak valid.",
                code="XENDIT_INVALID_RESPONSE",
            )
        return body


def build_xendit_checkout(settings: Any) -> XenditCheckoutAdapter:
    """Bangun adapter terkonfigurasi dari settings backend yang tepercaya.

    Raise ValueError bila konfigurasi wajib kosong (tanpa menyebut nilai).
    """
    required = {
        "base_url": settings.xendit_base_url,
        "secret_key": settings.xendit_secret_key,
        "payment_product": settings.xendit_payment_product,
        "api_version": settings.xendit_api_version,
    }
    missing = [
        name
        for name, value in required.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise ValueError(
            "Konfigurasi Xendit tidak lengkap: " + ", ".join(sorted(missing))
        )
    return XenditCheckoutAdapter(
        XenditAdapterConfig(
            base_url=settings.xendit_base_url,
            secret_key=settings.xendit_secret_key,
            payment_product=settings.xendit_payment_product,
            api_version=settings.xendit_api_version,
            environment=settings.xendit_environment,
            success_redirect_url=settings.xendit_success_redirect_url,
            failure_redirect_url=settings.xendit_failure_redirect_url,
            timeout_seconds=float(settings.xendit_timeout_seconds),
        )
    )
