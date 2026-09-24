"""Payment service: order lifecycle + Xendit adapter port + webhook inbox (Phase 2).

Kontrak (billing-plans.md):
- Nominal/currency/token dari DB package version, bukan client.
- Order `created` + merchant_reference unik SEBELUM request vendor.
- Webhook inbox dedupe dulu, acknowledge setelah durable acceptance.
- Satu paid order satu top-up (journal business_key).
- Out-of-order event tidak menurunkan `paid`.

DEC-07 (produk/API Xendit, auth webhook, status enum) masih open: adapter
didefinisikan sebagai Protocol. Implementasi vendor konkret wajib menunggu
SPK-03; tidak menebak payload/format vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.billing.models import (
    PaymentOrder,
    TokenPackageVersion,
    WebhookInbox,
)
from temanbule.modules.billing.wallet import WalletService
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid, sha256_hex

ORDER_CREATED = "created"
ORDER_PENDING = "pending"
ORDER_PAID = "paid"
ORDER_FAILED = "failed"
ORDER_EXPIRED = "expired"
ORDER_REFUNDED = "refunded"
ORDER_DISPUTED = "disputed"

WEBHOOK_RECEIVED = "received"
WEBHOOK_PROCESSING = "processing"
WEBHOOK_PROCESSED = "processed"
WEBHOOK_FAILED = "failed"
WEBHOOK_IGNORED = "ignored"


@dataclass(frozen=True)
class CheckoutResult:
    provider_payment_id: str
    checkout_url: str
    expires_at: datetime | None


class XenditCheckoutPort(Protocol):
    """Port ke produk checkout Xendit. Konkret menunggu DEC-07/SPK-03."""

    async def create_checkout(
        self,
        *,
        merchant_reference: str,
        amount_minor: int,
        currency: str,
        description: str,
        expires_at: datetime | None,
    ) -> CheckoutResult: ...

    async def lookup_payment(self, *, provider_payment_id: str) -> str:
        """Mengembalikan status otoritatif dari provider (untuk reconciliation)."""
        ...


@dataclass(frozen=True)
class WebhookEvent:
    provider: str
    provider_event_key: str
    raw_payload: bytes
    merchant_reference: str | None
    provider_payment_id: str | None
    reported_status: str
    amount_minor: int | None
    currency: str | None


class PaymentService:
    def __init__(self, session: AsyncSession, checkout: XenditCheckoutPort) -> None:
        self.session = session
        self.checkout = checkout
        self.wallets = WalletService(session)

    async def create_order(
        self, *, user_id: str, package_version_id: str, idempotency_key: str
    ) -> PaymentOrder:
        """Buat order created + request checkout vendor. Idempoten per (user, key)."""
        package = (
            await self.session.execute(
                select(TokenPackageVersion).where(
                    TokenPackageVersion.id == package_version_id,
                    TokenPackageVersion.status == "published",
                )
            )
        ).scalar_one_or_none()
        if package is None:
            raise NotFoundError("Paket tidak ditemukan atau belum published.")

        merchant_reference = sha256_hex(f"{user_id}:{idempotency_key}")[:64]
        existing = (
            await self.session.execute(
                select(PaymentOrder).where(
                    PaymentOrder.merchant_reference == merchant_reference
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        order = PaymentOrder(
            id=new_ulid(),
            user_id=user_id,
            package_version_id=package.id,
            merchant_reference=merchant_reference,
            amount_minor=package.amount_minor,
            currency=package.currency,
            token_units=package.token_units,
            state=ORDER_CREATED,
        )
        self.session.add(order)
        await self.session.flush()

        result = await self.checkout.create_checkout(
            merchant_reference=merchant_reference,
            amount_minor=package.amount_minor,
            currency=package.currency,
            description=f"TemanBule top-up {package.package_code}",
            expires_at=None,
        )
        order.provider_payment_id = result.provider_payment_id
        order.checkout_url = result.checkout_url
        order.expires_at = result.expires_at
        order.state = ORDER_PENDING
        await self.session.flush()
        return order

    async def accept_webhook(self, event: WebhookEvent) -> WebhookInbox:
        """Persist inbox + dedupe SEBELUM processing. Aman untuk delivery ganda."""
        payload_hash = sha256_hex(event.raw_payload.hex())
        existing = (
            await self.session.execute(
                select(WebhookInbox).where(
                    WebhookInbox.provider == event.provider,
                    WebhookInbox.provider_event_key == event.provider_event_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        inbox = WebhookInbox(
            id=new_ulid(),
            provider=event.provider,
            provider_event_key=event.provider_event_key,
            payload_hash=payload_hash,
            state=WEBHOOK_RECEIVED,
        )
        self.session.add(inbox)
        await self.session.flush()
        return inbox

    async def process_webhook(self, event: WebhookEvent) -> None:
        """Kredit saldo untuk payment sukses; tepat sekali per order.

        Unknown reference / amount mismatch → inbox failed, tanpa kredit
        (masuk jalur reconciliation manual, tidak auto-retry buta).
        """
        inbox = await self.accept_webhook(event)
        if inbox.state == WEBHOOK_PROCESSED:
            return
        inbox.state = WEBHOOK_PROCESSING
        await self.session.flush()

        try:
            await self._apply_payment_event(event)
        except (NotFoundError, ValidationError) as exc:
            inbox.state = WEBHOOK_FAILED
            await self.session.flush()
            raise exc
        inbox.state = WEBHOOK_PROCESSED
        inbox.processed_at = datetime.now(UTC)
        await self.session.flush()

    async def _apply_payment_event(self, event: WebhookEvent) -> None:
        if event.merchant_reference is None:
            raise ValidationError("Webhook tanpa merchant_reference.")
        order = (
            await self.session.execute(
                select(PaymentOrder)
                .where(PaymentOrder.merchant_reference == event.merchant_reference)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if order is None:
            raise NotFoundError("Order untuk merchant_reference tidak ditemukan.")

        if event.amount_minor is not None and event.amount_minor != order.amount_minor:
            raise ValidationError(
                "Amount webhook tidak cocok dengan order.",
                details=[{"field": "amount_minor", "message": "mismatch"}],
            )
        if event.currency is not None and event.currency != order.currency:
            raise ValidationError(
                "Currency webhook tidak cocok dengan order.",
                details=[{"field": "currency", "message": "mismatch"}],
            )

        # Out-of-order: paid tidak diturunkan oleh event gagal/expired terlambat.
        if order.state == ORDER_PAID:
            return

        if event.reported_status == "paid":
            # Konfirmasi status otoritatif sebelum kredit (billing-plans.md poin 4).
            authoritative = await self.checkout.lookup_payment(
                provider_payment_id=order.provider_payment_id or ""
            )
            if authoritative != "paid":
                raise ConflictError(
                    "Status otoritatif provider bukan paid.",
                    code="PAYMENT_NOT_CONFIRMED",
                )
            credited = await self.wallets.credit_topup(
                user_id=order.user_id, order_id=order.id, token_units=order.token_units
            )
            order.state = ORDER_PAID
            order.paid_at = datetime.now(UTC)
            await self.session.flush()
            if not credited:
                # Sudah dikredit sebelumnya (replay path lain); tetap konsisten.
                return
        elif event.reported_status in ("failed", "expired"):
            order.state = ORDER_FAILED if event.reported_status == "failed" else ORDER_EXPIRED
            await self.session.flush()
        else:
            raise ValidationError(
                "Status webhook tidak dikenal.",
                details=[{"field": "reported_status", "message": event.reported_status}],
            )
