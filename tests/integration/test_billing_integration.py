"""Integration tests billing: ledger/wallet/reservation pada PostgreSQL nyata.

Menutup exit criteria Phase 2:
- race saldo (dua reserve bersamaan tidak memakai saldo sama)
- double top-up prevention (satu paid order satu kredit)
- settle idempoten (charge kumulatif non-decreasing)
- ledger invariant (balanced, append-only)

Jalankan: TEST_DATABASE_URL=... pytest -m integration tests/integration/test_billing_integration.py
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from temanbule.modules.billing.ledger import LEDGER_ASSET
from temanbule.modules.billing.models import (
    LedgerJournal,
    RateCardVersion,
    UsageReservation,
    Wallet,
)
from temanbule.modules.billing.wallet import WalletService
from temanbule.modules.identity.models import User
from temanbule.platform.errors import ConflictError
from temanbule.platform.security import new_ulid, sha256_hex

pytestmark = pytest.mark.integration

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")

requires_db = pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL tidak diset")


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


async def _make_user(db: AsyncSession, email: str) -> User:
    user = User(id=new_ulid(), normalized_email=email)
    db.add(user)
    await db.flush()
    return user


async def _make_rate_card(db: AsyncSession, version: str) -> RateCardVersion:
    from datetime import UTC, datetime

    card = RateCardVersion(
        id=new_ulid(),
        version=version,
        effective_at=datetime.now(UTC),
        rounding_policy="half_up",
        status="published",
    )
    db.add(card)
    await db.flush()
    return card


@requires_db
async def test_topup_credit_once_and_balanced(db: AsyncSession) -> None:
    """Satu paid order satu top-up; replay tidak menambah saldo."""
    wallet_svc = WalletService(db)
    user = await _make_user(db, f"topup-{new_ulid()}@example.com")
    order_id = new_ulid()

    credited_first = await wallet_svc.credit_topup(
        user_id=user.id, order_id=order_id, token_units=1000
    )
    credited_replay = await wallet_svc.credit_topup(
        user_id=user.id, order_id=order_id, token_units=1000
    )

    assert credited_first is True
    assert credited_replay is False

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == user.id))
    ).scalar_one()
    assert wallet.available_units == 1000
    assert wallet.held_units == 0

    # Journal balanced: sum entries per journal = 0
    journal = (
        await db.execute(
            select(LedgerJournal).where(LedgerJournal.operation_ref == order_id)
        )
    ).scalar_one()
    from sqlalchemy import func

    from temanbule.modules.billing.models import LedgerEntry

    total = (
        await db.execute(
            select(func.coalesce(func.sum(LedgerEntry.signed_units), 0)).where(
                LedgerEntry.journal_id == journal.id
            )
        )
    ).scalar_one()
    assert total == 0


@requires_db
async def test_reserve_and_release_returns_funds(db: AsyncSession) -> None:
    wallet_svc = WalletService(db)
    user = await _make_user(db, f"reserve-{new_ulid()}@example.com")
    card = await _make_rate_card(db, f"v-{new_ulid()[:8]}")
    await wallet_svc.credit_topup(user_id=user.id, order_id=new_ulid(), token_units=500)

    quote = await wallet_svc.authorize_reservation(
        user_id=user.id,
        operation_id=f"op-{new_ulid()}",
        rate_card_id=card.id,
        units=300,
        lease_seconds=60,
    )
    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == user.id))
    ).scalar_one()
    assert wallet.available_units == 200
    assert wallet.held_units == 300

    # Reserve dengan operation_id sama = idempoten
    quote_again = await wallet_svc.authorize_reservation(
        user_id=user.id,
        operation_id=quote.operation_id,
        rate_card_id=card.id,
        units=300,
        lease_seconds=60,
    )
    assert quote_again.reservation_id == quote.reservation_id
    await db.refresh(wallet)
    assert wallet.held_units == 300

    await wallet_svc.release_reservation(reservation_id=quote.reservation_id, reason="test")
    await db.refresh(wallet)
    assert wallet.available_units == 500
    assert wallet.held_units == 0

    # Release ulang = no-op
    await wallet_svc.release_reservation(reservation_id=quote.reservation_id, reason="test")
    await db.refresh(wallet)
    assert wallet.available_units == 500


@requires_db
async def test_settle_cumulative_idempotent(db: AsyncSession) -> None:
    wallet_svc = WalletService(db)
    user = await _make_user(db, f"settle-{new_ulid()}@example.com")
    card = await _make_rate_card(db, f"v-{new_ulid()[:8]}")
    await wallet_svc.credit_topup(user_id=user.id, order_id=new_ulid(), token_units=1000)
    quote = await wallet_svc.authorize_reservation(
        user_id=user.id,
        operation_id=f"op-{new_ulid()}",
        rate_card_id=card.id,
        units=400,
        lease_seconds=60,
    )
    invocation_id = new_ulid()

    # Streaming: charge kumulatif 100 → 250 → 250 (replay frame terakhir)
    assert await wallet_svc.settle_reservation(
        reservation_id=quote.reservation_id, invocation_id=invocation_id, charge_units=100
    ) is True
    assert await wallet_svc.settle_reservation(
        reservation_id=quote.reservation_id, invocation_id=invocation_id, charge_units=250
    ) is True
    assert await wallet_svc.settle_reservation(
        reservation_id=quote.reservation_id, invocation_id=invocation_id, charge_units=250
    ) is False  # replay tidak mendebit

    wallet = (
        await db.execute(select(Wallet).where(Wallet.user_id == user.id))
    ).scalar_one()
    assert wallet.held_units == 150  # 400 - 250
    reservation = (
        await db.execute(
            select(UsageReservation).where(UsageReservation.id == quote.reservation_id)
        )
    ).scalar_one()
    assert reservation.settled_units == 250

    # Charge melebihi authorization ditolak
    with pytest.raises(ConflictError):
        await wallet_svc.settle_reservation(
            reservation_id=quote.reservation_id,
            invocation_id=invocation_id,
            charge_units=500,
        )


@requires_db
async def test_insufficient_balance_rejected(db: AsyncSession) -> None:
    wallet_svc = WalletService(db)
    user = await _make_user(db, f"insufficient-{new_ulid()}@example.com")
    card = await _make_rate_card(db, f"v-{new_ulid()[:8]}")
    await wallet_svc.credit_topup(user_id=user.id, order_id=new_ulid(), token_units=100)

    with pytest.raises(ConflictError, match="Saldo tidak mencukupi"):
        await wallet_svc.authorize_reservation(
            user_id=user.id,
            operation_id=f"op-{new_ulid()}",
            rate_card_id=card.id,
            units=101,
            lease_seconds=60,
        )


@requires_db
async def test_concurrent_reserve_same_balance(engine_url: str = DATABASE_URL) -> None:
    """Dua reserve bersamaan atas saldo yang sama: tepat satu yang menang."""
    if not DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL tidak diset")
    engine = create_async_engine(engine_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    user_id = new_ulid()
    card_id = new_ulid()
    async with factory() as session:
        from datetime import UTC, datetime

        session.add(User(id=user_id, normalized_email=f"race-{new_ulid()}@example.com"))
        session.add(
            RateCardVersion(
                id=card_id,
                version=f"v-{new_ulid()[:8]}",
                effective_at=datetime.now(UTC),
                rounding_policy="half_up",
                status="published",
            )
        )
        await session.flush()
        wallet_svc = WalletService(session)
        await wallet_svc.credit_topup(user_id=user_id, order_id=new_ulid(), token_units=100)
        await session.commit()

    async def try_reserve(op_id: str) -> str:
        async with factory() as session:
            wallet_svc = WalletService(session)
            try:
                await wallet_svc.authorize_reservation(
                    user_id=user_id,
                    operation_id=op_id,
                    rate_card_id=card_id,
                    units=100,
                    lease_seconds=60,
                )
                await session.commit()
                return "ok"
            except ConflictError:
                await session.rollback()
                return "conflict"

    results = await asyncio.gather(
        try_reserve(f"op-race-a-{new_ulid()}"),
        try_reserve(f"op-race-b-{new_ulid()}"),
    )
    # Saldo 100, dua reserve masing-masing 100 → tepat satu sukses
    assert sorted(results) == ["conflict", "ok"], f"race salah: {results}"

    async with factory() as session:
        wallet = (
            await session.execute(select(Wallet).where(Wallet.user_id == user_id))
        ).scalar_one()
        assert wallet.available_units == 0
        assert wallet.held_units == 100
    # Fixture sengaja tidak dihapus: ledger append-only (trigger DB) dan user unik
    # per test run. Database test dibersihkan di luar band bila diperlukan.
    await engine.dispose()


@requires_db
async def test_ledger_append_only_enforced_by_db(db: AsyncSession) -> None:
    """Trigger DB menolak UPDATE/DELETE pada ledger (defense in depth)."""
    wallet_svc = WalletService(db)
    user = await _make_user(db, f"append-{new_ulid()}@example.com")
    order_id = new_ulid()
    await wallet_svc.credit_topup(user_id=user.id, order_id=order_id, token_units=10)

    journal = (
        await db.execute(
            select(LedgerJournal).where(LedgerJournal.operation_ref == order_id)
        )
    ).scalar_one()
    journal.kind = "tampered"
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append_only_table"):
        await db.flush()
    await db.rollback()


def test_business_key_deterministic() -> None:
    from temanbule.modules.billing.ledger import business_key

    assert business_key("topup", "order-1") == business_key("topup", "order-1")
    assert business_key("topup", "order-1") != business_key("topup", "order-2")
    assert len(business_key("x")) == 64


def test_ledger_asset_constant() -> None:
    assert LEDGER_ASSET == "token"


def test_sha256_used_for_hashes() -> None:
    assert len(sha256_hex("payload")) == 64
