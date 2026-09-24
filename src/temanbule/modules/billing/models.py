"""Billing module models: packages, rate cards, wallets, ledger, payments (Phase 2).

Ledger, usage_records, usage_settlements append-only — trigger mencegah
UPDATE/DELETE di database. Wallet caches dan ledger selalu satu transaksi.
Sesuai .blueprint/postgresql-schema.md dan .blueprint/billing-plans.md.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from temanbule.platform.base import Base, TimestampMixin, utcnow


class TokenPackageVersion(Base):
    """Paket top-up terversi; published immutable."""

    __tablename__ = "token_package_versions"
    __table_args__ = (
        UniqueConstraint("package_code", "revision", name="uq_token_package_revision"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    package_code: Mapped[str] = mapped_column(String(40))
    revision: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    token_units: Mapped[int] = mapped_column(BigInteger)
    display_scale: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateCardVersion(Base):
    __tablename__ = "rate_card_versions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), unique=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    rounding_policy: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    items: Mapped[list[RateCardItem]] = relationship(back_populates="rate_card")


class RateCardItem(Base):
    __tablename__ = "rate_card_items"
    __table_args__ = (
        UniqueConstraint(
            "rate_card_id", "model_id", "capability", "meter", name="uq_rate_card_item"
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    rate_card_id: Mapped[str] = mapped_column(
        ForeignKey("rate_card_versions.id", ondelete="RESTRICT"), index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_model_configurations.id", ondelete="RESTRICT")
    )
    capability: Mapped[str] = mapped_column(String(20))
    meter: Mapped[str] = mapped_column(String(40))
    quantity_unit: Mapped[str] = mapped_column(String(20))
    cost_numerator: Mapped[int] = mapped_column(BigInteger)
    cost_denominator: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    rate_card: Mapped[RateCardVersion] = relationship(back_populates="items")


class Wallet(Base, TimestampMixin):
    """Saldo cache pengguna; kebenaran ada di ledger. Optimistic version."""

    __tablename__ = "wallets"
    __table_args__ = (
        CheckConstraint("available_units >= 0", name="ck_wallets_available_nonnegative"),
        CheckConstraint("held_units >= 0", name="ck_wallets_held_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), unique=True)
    asset: Mapped[str] = mapped_column(String(20))
    available_units: Mapped[int] = mapped_column(BigInteger, default=0)
    held_units: Mapped[int] = mapped_column(BigInteger, default=0)
    version: Mapped[int] = mapped_column(BigInteger, default=0)


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("wallet_id", "account_type", "asset", name="uq_ledger_account_logical"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    wallet_id: Mapped[str | None] = mapped_column(ForeignKey("wallets.id", ondelete="RESTRICT"))
    account_type: Mapped[str] = mapped_column(String(40))
    asset: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerJournal(Base):
    """Append-only; business_key UNIQUE memastikan dedupe logis."""

    __tablename__ = "ledger_journals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    business_key: Mapped[str] = mapped_column(String(255), unique=True)
    operation_ref: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(40))
    reversal_of: Mapped[str | None] = mapped_column(String(26))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entries: Mapped[list[LedgerEntry]] = relationship(back_populates="journal")


class LedgerEntry(Base):
    """Append-only; sum per journal/asset = 0 (diverifikasi aplikasi dalam transaksi)."""

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    journal_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_journals.id", ondelete="RESTRICT"), index=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), index=True
    )
    signed_units: Mapped[int] = mapped_column(BigInteger)
    asset: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    journal: Mapped[LedgerJournal] = relationship(back_populates="entries")


class UsageReservation(Base, TimestampMixin):
    __tablename__ = "usage_reservations"
    __table_args__ = (
        CheckConstraint(
            "settled_units + released_units <= authorized_units",
            name="ck_reservation_within_authorized",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    wallet_id: Mapped[str] = mapped_column(
        ForeignKey("wallets.id", ondelete="RESTRICT"), index=True
    )
    operation_id: Mapped[str] = mapped_column(String(64), unique=True)
    rate_card_id: Mapped[str] = mapped_column(
        ForeignKey("rate_card_versions.id", ondelete="RESTRICT")
    )
    authorized_units: Mapped[int] = mapped_column(BigInteger)
    settled_units: Mapped[int] = mapped_column(BigInteger, default=0)
    released_units: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(32), default="active", index=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiInvocation(Base):
    __tablename__ = "ai_invocations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(64), unique=True)
    runtime_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_snapshots.id", ondelete="RESTRICT"), index=True
    )
    capability: Mapped[str] = mapped_column(String(20))
    payer: Mapped[str] = mapped_column(String(20))  # user | platform
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="started")
    reservation_id: Mapped[str | None] = mapped_column(
        ForeignKey("usage_reservations.id", ondelete="RESTRICT"), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageRecord(Base):
    """Append-only; provider usage, bukan klaim client/LLM."""

    __tablename__ = "usage_records"
    __table_args__ = (
        UniqueConstraint("invocation_id", "meter", "sequence", name="uq_usage_record_sequence"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(
        ForeignKey("ai_invocations.id", ondelete="RESTRICT"), index=True
    )
    meter: Mapped[str] = mapped_column(String(40))
    meter_version: Mapped[str] = mapped_column(String(40))
    cumulative_quantity: Mapped[int] = mapped_column(BigInteger)
    sequence: Mapped[int] = mapped_column(Integer)
    provider_evidence_ref: Mapped[str | None] = mapped_column(Text)
    final: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageSettlement(Base):
    """Append-only; settlement terhadap invocation via journal."""

    __tablename__ = "usage_settlements"
    __table_args__ = (
        UniqueConstraint("invocation_id", "revision", name="uq_usage_settlement_revision"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    invocation_id: Mapped[str] = mapped_column(ForeignKey("ai_invocations.id", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    cumulative_charge: Mapped[int] = mapped_column(BigInteger)
    journal_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_journals.id", ondelete="RESTRICT"), index=True
    )
    rate_card_id: Mapped[str] = mapped_column(
        ForeignKey("rate_card_versions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentOrder(Base, TimestampMixin):
    __tablename__ = "payment_orders"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    package_version_id: Mapped[str] = mapped_column(
        ForeignKey("token_package_versions.id", ondelete="RESTRICT")
    )
    merchant_reference: Mapped[str] = mapped_column(String(128), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    token_units: Mapped[int] = mapped_column(BigInteger)
    checkout_url: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebhookInbox(Base):
    """Penerimaan webhook terpisah dari status payment; dedupe per provider event key."""

    __tablename__ = "webhook_inbox"
    __table_args__ = (
        UniqueConstraint("provider", "provider_event_key", name="uq_webhook_inbox_event"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_event_key: Mapped[str] = mapped_column(String(255))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_ref: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(32), default="received", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentRefund(Base, TimestampMixin):
    __tablename__ = "payment_refunds"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), index=True
    )
    provider_refund_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    request_key: Mapped[str] = mapped_column(String(128), unique=True)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    token_units: Mapped[int] = mapped_column(BigInteger)
    journal_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledger_journals.id", ondelete="RESTRICT")
    )
    state: Mapped[str] = mapped_column(String(32), default="requested")


class PaymentDispute(Base, TimestampMixin):
    __tablename__ = "payment_disputes"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("payment_orders.id", ondelete="RESTRICT"), index=True
    )
    provider_dispute_ref: Mapped[str] = mapped_column(String(128), unique=True)
    outstanding_units: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="open")
    evidence_ref: Mapped[str | None] = mapped_column(Text)
