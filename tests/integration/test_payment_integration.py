"""Integration tests payment: order + webhook inbox + kredit tepat sekali.

Menutup exit criteria Phase 2:
- double webhook (event sama dua kali → satu kredit)
- event berbeda untuk payment sama → tetap satu kredit
- out-of-order (paid lalu failed terlambat → tetap paid)
- amount/currency mismatch → tanpa kredit, inbox failed
- unknown merchant_reference → tanpa kredit

CheckoutPort di sini TEST DOUBLE berlabel jelas untuk pengujian terisolasi;
bukan bukti kompatibilitas vendor Xendit (menunggu DEC-07/SPK-03).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.billing.models import (
    PaymentOrder,
    TokenPackageVersion,
    Wallet,
    WebhookInbox,
)
from temanbule.modules.billing.payments import (
    ORDER_PAID,
    WEBHOOK_FAILED,
    WEBHOOK_PROCESSED,
    CheckoutResult,
    PaymentService,
    WebhookEvent,
)
from temanbule.modules.identity.models import User
from temanbule.platform.errors import NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")


class FakeCheckout:
    """TEST DOUBLE — bukan bukti kompatibilitas Xendit (SPK-03 pending)."""

    def __init__(self, authoritative_status: str = "paid") -> None:
        self.created: list[dict[str, object]] = []
        self.authoritative_status = authoritative_status
        self.lookup_count = 0

    async def create_checkout(
        self,
        *,
        merchant_reference: str,
        amount_minor: int,
        currency: str,
        description: str,
        expires_at: datetime | None,
    ) -> CheckoutResult:
        self.created.append({"merchant_reference": merchant_reference})
        return CheckoutResult(
            provider_payment_id=f"xendit-{merchant_reference[:16]}",
            checkout_url=f"https://checkout.example.test/{merchant_reference[:16]}",
            expires_at=None,
        )

    async def lookup_payment(self, *, provider_payment_id: str) -> str:
        self.lookup_count += 1
        return self.authoritative_status


@pytest.fixture()
async def db() -> AsyncGenerator[AsyncSession, None]:
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL tidak diset")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@dataclass(frozen=True)
class Fixture:
    user: User
    package: TokenPackageVersion


async def _fixture(db: AsyncSession, *, token_units: int = 700) -> Fixture:
    user = User(id=new_ulid(), normalized_email=f"pay-{new_ulid()}@example.com")
    package = TokenPackageVersion(
        id=new_ulid(),
        package_code=f"pack-{new_ulid()[:8]}",
        revision=1,
        currency="IDR",
        amount_minor=50000,
        token_units=token_units,
        status="published",
    )
    db.add_all([user, package])
    await db.flush()
    return Fixture(user, package)


def _event(order: PaymentOrder, *, key: str, status: str = "paid") -> WebhookEvent:
    return WebhookEvent(
        provider="xendit",
        provider_event_key=key,
        raw_payload=b'{"test":"payload"}',
        merchant_reference=order.merchant_reference,
        provider_payment_id=order.provider_payment_id,
        reported_status=status,
        amount_minor=order.amount_minor,
        currency=order.currency,
    )


@requires_db
async def test_create_order_idempotent(db: AsyncSession) -> None:
    fx = await _fixture(db)
    checkout = FakeCheckout()
    svc = PaymentService(db, checkout)

    first = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="key-1"
    )
    again = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="key-1"
    )
    assert first.id == again.id
    assert len(checkout.created) == 1  # vendor tidak dipanggil dua kali
    assert first.state == "pending"
    assert first.amount_minor == 50000
    assert first.token_units == 700


@requires_db
async def test_reject_unpublished_package(db: AsyncSession) -> None:
    fx = await _fixture(db)
    fx.package.status = "draft"
    await db.flush()
    svc = PaymentService(db, FakeCheckout())
    with pytest.raises(NotFoundError):
        await svc.create_order(
            user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="k"
        )


@requires_db
async def test_double_webhook_single_credit(db: AsyncSession) -> None:
    fx = await _fixture(db)
    svc = PaymentService(db, FakeCheckout())
    order = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="k2"
    )

    event = _event(order, key="evt-1")
    await svc.process_webhook(event)
    await svc.process_webhook(event)  # delivery ganda identik

    # Event BERBEDA untuk payment yang sama
    event2 = _event(order, key="evt-2")
    await svc.process_webhook(event2)

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx.user.id))
    ).scalar_one()
    assert wallet.available_units == 700  # tepat satu kredit

    inbox_rows = (
        (await db.execute(select(WebhookInbox).where(WebhookInbox.provider == "xendit")))
        .scalars()
        .all()
    )
    assert {r.provider_event_key for r in inbox_rows} == {"evt-1", "evt-2"}
    assert all(r.state == WEBHOOK_PROCESSED for r in inbox_rows)
    await db.refresh(order)
    assert order.state == ORDER_PAID
    assert order.paid_at is not None


@requires_db
async def test_out_of_order_event_does_not_downgrade_paid(db: AsyncSession) -> None:
    fx = await _fixture(db)
    svc = PaymentService(db, FakeCheckout())
    order = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="k3"
    )
    await svc.process_webhook(_event(order, key="evt-paid"))
    await svc.process_webhook(_event(order, key="evt-failed-late", status="failed"))

    await db.refresh(order)
    assert order.state == ORDER_PAID
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx.user.id))
    ).scalar_one()
    assert wallet.available_units == 700


@requires_db
async def test_amount_mismatch_no_credit(db: AsyncSession) -> None:
    fx = await _fixture(db)
    svc = PaymentService(db, FakeCheckout())
    order = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="k4"
    )
    bad = WebhookEvent(
        provider="xendit",
        provider_event_key="evt-bad",
        raw_payload=b"{}",
        merchant_reference=order.merchant_reference,
        provider_payment_id=order.provider_payment_id,
        reported_status="paid",
        amount_minor=1,  # mismatch
        currency="IDR",
    )
    with pytest.raises(ValidationError, match="Amount"):
        await svc.process_webhook(bad)

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx.user.id))
    ).scalar_one_or_none()
    assert wallet is None or wallet.available_units == 0
    inbox = (
        await db.execute(
            select(WebhookInbox).where(WebhookInbox.provider_event_key == "evt-bad")
        )
    ).scalar_one()
    assert inbox.state == WEBHOOK_FAILED


@requires_db
async def test_unknown_merchant_reference_no_credit(db: AsyncSession) -> None:
    fx = await _fixture(db)
    svc = PaymentService(db, FakeCheckout())
    event = WebhookEvent(
        provider="xendit",
        provider_event_key="evt-unknown",
        raw_payload=b"{}",
        merchant_reference="ref-tidak-ada",
        provider_payment_id="x",
        reported_status="paid",
        amount_minor=50000,
        currency="IDR",
    )
    with pytest.raises(NotFoundError):
        await svc.process_webhook(event)
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx.user.id))
    ).scalar_one_or_none()
    assert wallet is None


@requires_db
async def test_unconfirmed_payment_no_credit(db: AsyncSession) -> None:
    """Provider lookup mengatakan belum paid → tanpa kredit walau webhook klaim paid."""
    fx = await _fixture(db)
    svc = PaymentService(db, FakeCheckout(authoritative_status="pending"))
    order = await svc.create_order(
        user_id=fx.user.id, package_version_id=fx.package.id, idempotency_key="k5"
    )
    from temanbule.platform.errors import ConflictError

    with pytest.raises(ConflictError, match="otoritatif"):
        await svc.process_webhook(_event(order, key="evt-claim"))
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == fx.user.id))
    ).scalar_one_or_none()
    assert wallet is None
