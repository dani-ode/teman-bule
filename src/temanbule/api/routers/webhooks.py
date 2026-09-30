"""Webhook router: Xendit payment callbacks (Phase 2).

Kontrak (api-events.md):
- POST /v1/webhooks/xendit: callback auth + provider confirmation + durable inbox.
- Reference/amount/currency validation; no user JWT.
- Limit body size and redact payload secrets.
"""

from __future__ import annotations

import hmac
import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field

from temanbule.api.deps import SessionDep, SettingsDep
from temanbule.modules.billing.payments import (
    PaymentService,
    WebhookEvent,
    XenditCheckoutPort,
)
from temanbule.platform.errors import (
    FeatureUnavailableError,
    UnauthorizedError,
    ValidationError,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class XenditWebhookPayload(BaseModel):
    """Payload webhook Xendit Invoice API v2.

    Field wajib minimal untuk pemrosesan; field tambahan diabaikan.
    """

    id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    amount: int | None = None
    currency: str | None = None
    paid_at: str | None = None
    payment_id: str | None = None
    payment_channel: str | None = None
    payment_method: str | None = None


class WebhookAcceptedResponse(BaseModel):
    status: str = "accepted"


def _get_checkout_adapter(request: Request) -> XenditCheckoutPort:
    adapter: XenditCheckoutPort | None = getattr(request.app.state, "xendit_checkout", None)
    if adapter is None:
        raise FeatureUnavailableError(
            "Payment gateway belum dikonfigurasi (menunggu DEC-07).",
        )
    return adapter


def _verify_webhook_token(
    settings_token: str,
    x_callback_token: str | None,
) -> None:
    """Verifikasi callback token Xendit (constant-time compare)."""
    if not settings_token:
        raise FeatureUnavailableError(
            "Webhook token belum dikonfigurasi.",
        )
    if x_callback_token is None:
        raise UnauthorizedError("Callback token diperlukan.")
    if not hmac.compare_digest(x_callback_token, settings_token):
        raise UnauthorizedError("Callback token tidak valid.")


def _map_xendit_status(status: str) -> str:
    """Map status Xendit ke status internal (paid/failed/expired)."""
    mapping: dict[str, str] = {
        "PAID": "paid",
        "SETTLED": "paid",
        "EXPIRED": "expired",
        "FAILED": "failed",
    }
    return mapping.get(status.upper(), "unknown")


def _parse_amount_minor(amount_major: int | None, currency: str | None) -> int | None:
    """Konversi amount major ke minor; Xendit menggunakan major unit."""
    if amount_major is None or currency is None:
        return None
    # Zero-decimal currencies: 1 major = 1 minor
    zero_decimal = {
        "BIF", "CLP", "DJF", "GNF", "IDR", "JPY", "KMF", "KRW",
        "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
    }
    if currency.upper() in zero_decimal:
        return amount_major
    return amount_major * 100


@router.post("/xendit", response_model=WebhookAcceptedResponse, status_code=200)
async def xendit_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    x_callback_token: Annotated[str | None, Header(alias="X-Callback-Token")] = None,
) -> WebhookAcceptedResponse:
    """Terima dan proses webhook Xendit.

    Alur:
    1. Verifikasi callback token
    2. Parse dan validasi payload
    3. Simpan ke inbox (dedupe)
    4. Proses payment event
    """
    _verify_webhook_token(settings.xendit_webhook_token, x_callback_token)

    # Baca raw body untuk dedupe hash
    raw_body = await request.body()
    if len(raw_body) > settings.app_max_request_bytes:
        raise ValidationError("Payload terlalu besar.")

    try:
        body_dict: dict[str, Any] = json.loads(raw_body)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationError("Payload bukan JSON valid.") from exc

    payload = XenditWebhookPayload.model_validate(body_dict)

    checkout = _get_checkout_adapter(request)
    service = PaymentService(session, checkout)

    event = WebhookEvent(
        provider="xendit",
        provider_event_key=payload.id,
        raw_payload=raw_body,
        merchant_reference=payload.external_id,
        provider_payment_id=payload.payment_id or payload.id,
        reported_status=_map_xendit_status(payload.status),
        amount_minor=_parse_amount_minor(payload.amount, payload.currency),
        currency=payload.currency.upper() if payload.currency else None,
    )

    await service.process_webhook(event)
    await session.commit()

    return WebhookAcceptedResponse()
