"""Billing router: packages, wallet, top-up orders, ledger (Phase 2)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from temanbule.api.deps import CurrentUser, SessionDep
from temanbule.modules.billing.models import TokenPackageVersion, Wallet, LedgerJournal, LedgerEntry
from temanbule.modules.billing.payments import PaymentService, XenditCheckoutPort
from temanbule.platform.errors import FeatureUnavailableError, NotFoundError, ValidationError

router = APIRouter(prefix="/v1/billing", tags=["billing"])


class PackageResponse(BaseModel):
    package_version_id: str
    package_code: str
    revision: int
    currency: str
    amount_minor: int
    token_units: int
    display_scale: int


class LedgerEntryResponse(BaseModel):
    journal_id: str
    kind: str
    signed_units: int
    asset: str
    created_at: str


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


@router.get("/packages", response_model=list[PackageResponse])
async def list_packages(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[PackageResponse]:
    rows = (
        (
            await session.execute(
                select(TokenPackageVersion)
                .where(TokenPackageVersion.status == "published")
                .order_by(TokenPackageVersion.amount_minor.asc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        PackageResponse(
            package_version_id=p.id,
            package_code=p.package_code,
            revision=p.revision,
            currency=p.currency,
            amount_minor=p.amount_minor,
            token_units=p.token_units,
            display_scale=p.display_scale,
        )
        for p in rows
    ]


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


@router.get("/wallet/ledger", response_model=list[LedgerEntryResponse])
async def get_wallet_ledger(
    current_user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[LedgerEntryResponse]:
    wallet = (
        await session.execute(select(Wallet).where(Wallet.user_id == current_user.id))
    ).scalar_one_or_none()
    if wallet is None:
        raise NotFoundError("Wallet belum ada.")
    rows = (
        (
            await session.execute(
                select(LedgerEntry, LedgerJournal)
                .join(LedgerJournal, LedgerEntry.journal_id == LedgerJournal.id)
                .where(LedgerEntry.account_id.in_(
                    select(LedgerEntry.account_id).where(
                        LedgerEntry.account_id.in_(
                            select(LedgerEntry.account_id).where(
                                LedgerEntry.account_id == wallet.id
                            )
                        )
                    )
                ))
                .order_by(LedgerEntry.created_at.desc())
                .limit(min(limit, 200))
            )
        )
        .all()
    )
    return [
        LedgerEntryResponse(
            journal_id=j.id,
            kind=j.kind,
            signed_units=e.signed_units,
            asset=e.asset,
            created_at=e.created_at.isoformat(),
        )
        for e, j in rows
    ]


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
