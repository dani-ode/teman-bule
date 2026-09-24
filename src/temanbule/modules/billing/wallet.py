"""Wallet service: top-up credit, reservation authorize/settle/release (Phase 2).

Setiap mutasi saldo = journal double-entry + wallet cache update dalam SATU
transaksi SQL (via session caller; service tidak commit sendiri).
Replay aman: business_key unik mengembalikan hasil existing tanpa debit ganda.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.billing.ledger import (
    ACCOUNT_PLATFORM_CLEARING,
    ACCOUNT_PLATFORM_REVENUE,
    ACCOUNT_USER_AVAILABLE,
    ACCOUNT_USER_HELD,
    EntrySpec,
    LedgerService,
    business_key,
)
from temanbule.modules.billing.models import UsageReservation, Wallet
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

RESERVATION_ACTIVE = "active"
RESERVATION_SETTLING = "settling"
RESERVATION_SETTLED = "settled"
RESERVATION_RELEASED = "released"
RESERVATION_RECONCILIATION = "reconciliation_required"


@dataclass(frozen=True)
class ReservationQuote:
    reservation_id: str
    operation_id: str
    authorized_units: int


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ledger = LedgerService(session)

    async def get_or_create_wallet(self, user_id: str) -> Wallet:
        stmt = select(Wallet).where(Wallet.user_id == user_id)
        wallet = (await self.session.execute(stmt)).scalar_one_or_none()
        if wallet is not None:
            return wallet
        from temanbule.modules.billing.ledger import LEDGER_ASSET

        wallet = Wallet(
            id=new_ulid(),
            user_id=user_id,
            asset=LEDGER_ASSET,
            available_units=0,
            held_units=0,
            version=0,
        )
        self.session.add(wallet)
        await self.session.flush()
        return wallet

    async def credit_topup(self, *, user_id: str, order_id: str, token_units: int) -> bool:
        """Kredit top-up paid order. Satu paid order satu top-up.

        Mengembalikan True bila kredit baru diterapkan, False bila replay
        (journal untuk order ini sudah ada) tanpa debit ganda.
        """
        if token_units <= 0:
            raise ValidationError("token_units top-up harus positif.")
        key = business_key("topup", order_id)
        from temanbule.modules.billing.models import LedgerJournal

        journal_exists = (
            await self.session.execute(
                select(LedgerJournal.id).where(LedgerJournal.business_key == key)
            )
        ).scalar_one_or_none()
        if journal_exists is not None:
            return False
        wallet = await self.get_or_create_wallet(user_id)
        locked = await self.ledger.lock_wallet(wallet.id)
        await self.ledger.post_journal(
            key=key,
            operation_ref=order_id,
            kind="topup",
            entries=[
                EntrySpec(ACCOUNT_USER_AVAILABLE, token_units, wallet_id=locked.id),
                EntrySpec(ACCOUNT_PLATFORM_CLEARING, -token_units),
            ],
        )
        await self.ledger.apply_wallet_delta(locked, available_delta=token_units)
        return True

    async def authorize_reservation(
        self,
        *,
        user_id: str,
        operation_id: str,
        rate_card_id: str,
        units: int,
        lease_seconds: int,
    ) -> ReservationQuote:
        """Pindahkan available → held secara atomik. Dua request bersamaan aman."""
        if units <= 0:
            raise ValidationError("authorized units harus positif.")
        stmt = select(UsageReservation).where(UsageReservation.operation_id == operation_id)
        existing = (await self.session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            return ReservationQuote(existing.id, existing.operation_id, existing.authorized_units)

        wallet = await self.get_or_create_wallet(user_id)
        locked = await self.ledger.lock_wallet(wallet.id)
        await self.ledger.post_journal(
            key=business_key("reserve", operation_id),
            operation_ref=operation_id,
            kind="reservation_authorize",
            entries=[
                EntrySpec(ACCOUNT_USER_HELD, units, wallet_id=locked.id),
                EntrySpec(ACCOUNT_USER_AVAILABLE, -units, wallet_id=locked.id),
            ],
        )
        await self.ledger.apply_wallet_delta(locked, available_delta=-units, held_delta=units)
        reservation = UsageReservation(
            id=new_ulid(),
            wallet_id=locked.id,
            operation_id=operation_id,
            rate_card_id=rate_card_id,
            authorized_units=units,
            state=RESERVATION_ACTIVE,
            lease_until=datetime.now(UTC) + timedelta(seconds=lease_seconds),
        )
        self.session.add(reservation)
        await self.session.flush()
        return ReservationQuote(reservation.id, operation_id, units)

    async def settle_reservation(
        self, *, reservation_id: str, invocation_id: str, charge_units: int
    ) -> bool:
        """Pindahkan held → platform revenue (biaya terbukti).

        Idempoten per invocation: pemanggilan ulang dengan charge kumulatif
        yang sama tidak mendebit ganda. Charge harus non-decreasing.
        Mengembalikan True bila ada debit baru pada panggilan ini.
        """
        if charge_units < 0:
            raise ValidationError("charge_units tidak boleh negatif.")
        reservation = await self._lock_reservation(reservation_id)
        if charge_units <= reservation.settled_units:
            return False
        if reservation.state not in (RESERVATION_ACTIVE, RESERVATION_SETTLING):
            raise ConflictError(
                "Reservation tidak dapat disettle dari state saat ini.",
                details=[{"field": "state", "message": reservation.state}],
            )
        if charge_units > reservation.authorized_units - reservation.released_units:
            raise ConflictError(
                "Charge kumulatif melebihi authorization.",
                details=[{"field": "charge_units", "message": str(charge_units)}],
            )
        delta = charge_units - reservation.settled_units
        wallet = await self.ledger.lock_wallet(reservation.wallet_id)
        await self.ledger.post_journal(
            key=business_key("settle", invocation_id, str(charge_units)),
            operation_ref=invocation_id,
            kind="reservation_settle",
            entries=[
                EntrySpec(ACCOUNT_PLATFORM_REVENUE, delta),
                EntrySpec(ACCOUNT_USER_HELD, -delta, wallet_id=wallet.id),
            ],
        )
        await self.ledger.apply_wallet_delta(wallet, held_delta=-delta)
        reservation.settled_units = charge_units
        if reservation.settled_units + reservation.released_units >= reservation.authorized_units:
            reservation.state = RESERVATION_SETTLED
        else:
            reservation.state = RESERVATION_SETTLING
        await self.session.flush()
        return True

    async def release_reservation(self, *, reservation_id: str, reason: str) -> None:
        """Kembalikan sisa held → available. Idempoten; settled tidak diubah."""
        del reason  # dicatat caller via audit
        reservation = await self._lock_reservation(reservation_id)
        if reservation.state in (RESERVATION_RELEASED, RESERVATION_SETTLED):
            return
        remaining = (
            reservation.authorized_units - reservation.settled_units - reservation.released_units
        )
        if remaining <= 0:
            reservation.state = RESERVATION_SETTLED
            await self.session.flush()
            return
        wallet = await self.ledger.lock_wallet(reservation.wallet_id)
        await self.ledger.post_journal(
            key=business_key("release", reservation.operation_id),
            operation_ref=reservation.operation_id,
            kind="reservation_release",
            entries=[
                EntrySpec(ACCOUNT_USER_AVAILABLE, remaining, wallet_id=wallet.id),
                EntrySpec(ACCOUNT_USER_HELD, -remaining, wallet_id=wallet.id),
            ],
        )
        await self.ledger.apply_wallet_delta(
            wallet, available_delta=remaining, held_delta=-remaining
        )
        reservation.released_units += remaining
        reservation.state = (
            RESERVATION_SETTLED if reservation.settled_units > 0 else RESERVATION_RELEASED
        )
        await self.session.flush()

    async def mark_reconciliation_required(self, *, reservation_id: str) -> None:
        reservation = await self._lock_reservation(reservation_id)
        if reservation.state in (RESERVATION_SETTLED, RESERVATION_RELEASED):
            return
        reservation.state = RESERVATION_RECONCILIATION
        await self.session.flush()

    async def _lock_reservation(self, reservation_id: str) -> UsageReservation:
        stmt = (
            select(UsageReservation)
            .where(UsageReservation.id == reservation_id)
            .with_for_update()
        )
        reservation = (await self.session.execute(stmt)).scalar_one_or_none()
        if reservation is None:
            raise NotFoundError("Reservation tidak ditemukan.")
        await self.session.refresh(reservation)
        return reservation
