"""Ledger service: double-entry append-only journals + wallet cache (Phase 2).

Aturan mengikat (billing-plans.md):
- Ledger append-only; adjustment/refund memakai compensating journal.
- Wallet materialized balance hanya berubah dalam transaksi ledger yang sama.
- Journal unik per business_key mencegah debit ganda.
- Sum entries per journal/asset harus nol (balanced) sebelum commit.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.billing.models import (
    LedgerAccount,
    LedgerEntry,
    LedgerJournal,
    Wallet,
)
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

LEDGER_ASSET = "token"

# Account types (taxonomy billing)
ACCOUNT_USER_AVAILABLE = "user_available"
ACCOUNT_USER_HELD = "user_held"
ACCOUNT_PLATFORM_CLEARING = "platform_clearing"
ACCOUNT_PLATFORM_REVENUE = "platform_revenue"
ACCOUNT_PLATFORM_REFUND = "platform_refund"

WALLET_ACCOUNT_TYPES = {ACCOUNT_USER_AVAILABLE, ACCOUNT_USER_HELD}
PLATFORM_ACCOUNT_TYPES = {
    ACCOUNT_PLATFORM_CLEARING,
    ACCOUNT_PLATFORM_REVENUE,
    ACCOUNT_PLATFORM_REFUND,
}


def business_key(*parts: str) -> str:
    """Business key deterministik untuk dedupe journal."""
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


@dataclass(frozen=True)
class EntrySpec:
    account_type: str
    signed_units: int
    wallet_id: str | None = None


class LedgerService:
    """Menulis journal balanced + update wallet cache dalam satu session."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create_account(
        self, account_type: str, wallet_id: str | None = None
    ) -> LedgerAccount:
        stmt = select(LedgerAccount).where(
            LedgerAccount.account_type == account_type,
            (
                LedgerAccount.wallet_id.is_(None)
                if wallet_id is None
                else LedgerAccount.wallet_id == wallet_id
            ),
            LedgerAccount.asset == LEDGER_ASSET,
        )
        account = (await self.session.execute(stmt)).scalar_one_or_none()
        if account is not None:
            return account
        if account_type in WALLET_ACCOUNT_TYPES and wallet_id is None:
            raise ValidationError(
                "Akun wallet memerlukan wallet_id.",
                details=[{"field": "wallet_id", "message": "required untuk akun user"}],
            )
        if account_type in PLATFORM_ACCOUNT_TYPES and wallet_id is not None:
            raise ValidationError(
                "Akun platform tidak boleh terikat wallet user.",
                details=[{"field": "wallet_id", "message": "harus null untuk akun platform"}],
            )
        account = LedgerAccount(
            id=new_ulid(), wallet_id=wallet_id, account_type=account_type, asset=LEDGER_ASSET
        )
        self.session.add(account)
        await self.session.flush()
        return account

    async def post_journal(
        self,
        *,
        key: str,
        operation_ref: str,
        kind: str,
        entries: list[EntrySpec],
        reversal_of: str | None = None,
    ) -> LedgerJournal:
        """Posting journal double-entry; idempoten via business_key unique.

        Mengembalikan journal existing bila business_key sudah ada (replay aman).
        Gagal eksplisit bila entries tidak balanced per asset.
        """
        existing = (
            await self.session.execute(
                select(LedgerJournal).where(LedgerJournal.business_key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        if not entries:
            raise ValidationError("Journal wajib punya minimal satu entry.")
        balance: dict[str, int] = {}
        for entry in entries:
            if entry.signed_units == 0:
                raise ValidationError("Entry ledger tidak boleh nol.")
            balance[LEDGER_ASSET] = balance.get(LEDGER_ASSET, 0) + entry.signed_units
        if any(total != 0 for total in balance.values()):
            raise ValidationError(
                "Journal tidak balanced per asset.",
                details=[{"field": "entries", "message": f"sum harus 0, aktual {balance}"}],
            )

        journal = LedgerJournal(
            id=new_ulid(),
            business_key=key,
            operation_ref=operation_ref,
            kind=kind,
            reversal_of=reversal_of,
        )
        self.session.add(journal)
        await self.session.flush()

        for entry in entries:
            account = await self.get_or_create_account(entry.account_type, entry.wallet_id)
            self.session.add(
                LedgerEntry(
                    id=new_ulid(),
                    journal_id=journal.id,
                    account_id=account.id,
                    signed_units=entry.signed_units,
                    asset=LEDGER_ASSET,
                )
            )
        await self.session.flush()
        return journal

    async def lock_wallet(self, wallet_id: str) -> Wallet:
        """Row lock untuk perubahan saldo atomik (race-safe).

        Setelah lock didapat, object di-refresh dari DB agar tidak memakai
        snapshot stale dari identity map session ( penyebab race terbukti di
        test_concurrent_reserve_same_balance).
        """
        stmt = select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        wallet = (await self.session.execute(stmt)).scalar_one_or_none()
        if wallet is None:
            raise NotFoundError("Wallet tidak ditemukan.")
        await self.session.refresh(wallet)
        return wallet

    async def apply_wallet_delta(
        self, wallet: Wallet, *, available_delta: int = 0, held_delta: int = 0
    ) -> None:
        """Conditional UPDATE atomik di level SQL.

        Database mengevaluasi saldo terbaru pada baris terkunci; update gagal
        (0 baris) bila saldo akan negatif. Tidak bergantung nilai in-memory.
        """
        stmt = (
            update(Wallet)
            .where(
                Wallet.id == wallet.id,
                Wallet.available_units + available_delta >= 0,
                Wallet.held_units + held_delta >= 0,
            )
            .values(
                available_units=Wallet.available_units + available_delta,
                held_units=Wallet.held_units + held_delta,
                version=Wallet.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        result: CursorResult[object] = await self.session.execute(stmt)  # type: ignore[assignment]
        if result.rowcount != 1:
            raise ConflictError(
                "Saldo tidak mencukupi.",
                code="INSUFFICIENT_BALANCE",
                details=[{"field": "wallet", "message": "available/held akan negatif"}],
            )
        await self.session.refresh(wallet)


def journal_fingerprint(journal: LedgerJournal, entries: list[LedgerEntry]) -> str:
    payload = json.dumps(
        {
            "journal_id": journal.id,
            "business_key": journal.business_key,
            "entries": sorted(
                (e.account_id, e.signed_units, e.asset) for e in entries
            ),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
