"""Billing router: wallet balance + top-up orders (Phase 2).

Nominal/currency/token selalu dari DB package version — tidak dari client.
Xendit adapter konkret menunggu DEC-07/SPK-03; endpoint gagal eksplisit
(FEATURE_UNAVAILABLE) bila adapter belum terpasang, tanpa fallback.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.billing.models import Wallet
from temanbule.modules.billing.payments import PaymentService, XenditCheckoutPort
from temanbule.platform.errors import FeatureUnavailableError, NotFoundError, ValidationError

router = APIRouter(prefix="/v1/billing", tags=["billing"])


class WalletResponse(BaseModel):
    asset: str
    available_units: int
    held_units: int
    version: int


class TopupOrderRequest(BaseModel):
    package_version_id: str = Field(min_length=1, max_length=26)


class TopupOrderResponse(BaseModel):
    order_id: str
    merchant_reference: str
    state: str
    amount_minor: int
    currency: str
    token_units: int
    checkout_url: str | None


def _get_checkout_adapter(request: Request) -> XenditCheckoutPort:
    adapter: XenditCheckoutPort | None = getattr(request.app.state, "xendit_checkout", None)
    if adapter is None:
        raise FeatureUnavailableError(
            "Payment gateway belum dikonfigurasi (menunggu DEC-07).",
        )
    return adapter


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(current_user: CurrentUser, session: SessionDep) -> WalletResponse:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()
    if wallet is None:
        raise NotFoundError("Wallet belum ada; lakukan top-up pertama.")
    return WalletResponse(
        asset=wallet.asset,
        available_units=wallet.available_units,
        held_units=wallet.held_units,
        version=wallet.version,
    )


@router.post("/topup", response_model=TopupOrderResponse, status_code=201)
async def create_topup_order(
    body: TopupOrderRequest,
    current_user: CurrentUser,
    session: SessionDep,
    request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> TopupOrderResponse:
    if not idempotency_key:
        raise ValidationError(
            "Header Idempotency-Key wajib.",
            details=[{"field": "Idempotency-Key", "message": "required"}],
        )
    checkout = _get_checkout_adapter(request)
    service = PaymentService(session, checkout)
    order = await service.create_order(
        user_id=current_user.id,
        package_version_id=body.package_version_id,
        idempotency_key=idempotency_key,
    )
    await session.commit()
    return TopupOrderResponse(
        order_id=order.id,
        merchant_reference=order.merchant_reference,
        state=order.state,
        amount_minor=order.amount_minor,
        currency=order.currency,
        token_units=order.token_units,
        checkout_url=order.checkout_url,
    )
