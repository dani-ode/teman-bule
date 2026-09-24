"""Facts service (Phase 3): user facts dengan provenance dan consent rules.

Kontrak (callcraft-tools.md, langflow-flows.md):
- Inferred facts TIDAK boleh silently overwrite user-confirmed facts.
- Low-confidence → status proposed; konfirmasi eksplisit user mengungguli
  inferensi.
- Provenance (source message ids) wajib; supersedes terlacak, bukan mutasi
  diam-diam.
- Dedupe: satu fact_key aktif (non-superseded/rejected) per user.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.conversations.models import UserFact
from temanbule.platform.errors import ConflictError, NotFoundError, ValidationError
from temanbule.platform.security import new_ulid

STATUS_PROPOSED = "proposed"
STATUS_CONFIRMED = "confirmed"
STATUS_SUPERSEDED = "superseded"
STATUS_REJECTED = "rejected"
ACTIVE_STATUSES = (STATUS_PROPOSED, STATUS_CONFIRMED)

CONFIDENCE_CONFIRM_THRESHOLD = 0.8


class FactsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_fact(
        self,
        *,
        user_id: str,
        fact_key: str,
        value: str,
        confidence: float,
        provenance_ref: str,
        source_version: str,
        proposed_status: str = STATUS_PROPOSED,
    ) -> tuple[UserFact, bool]:
        """Upsert fact dengan consent rules. Return (fact, created)."""
        if not fact_key.strip():
            raise ValidationError("fact_key kosong.")
        if not value.strip():
            raise ValidationError("value kosong.")
        if not 0 <= confidence <= 1:
            raise ValidationError(
                "Confidence di luar batas.",
                details=[{"field": "confidence", "message": "harus 0..1"}],
            )
        if proposed_status not in (STATUS_PROPOSED, STATUS_CONFIRMED):
            raise ValidationError(
                "proposed_status tidak valid.",
                details=[{"field": "proposed_status", "message": proposed_status}],
            )
        if not provenance_ref.strip():
            raise ValidationError("provenance_ref wajib untuk audit.")

        existing = (
            await self.session.execute(
                select(UserFact).where(
                    UserFact.user_id == user_id,
                    UserFact.fact_key == fact_key,
                    UserFact.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            # Consent rule: fact confirmed tidak ditimpa inferensi baru.
            if existing.status == STATUS_CONFIRMED and proposed_status == STATUS_PROPOSED:
                if existing.value == value:
                    return existing, False
                raise ConflictError(
                    "Fact sudah dikonfirmasi user; inferensi baru tidak dapat menimpa.",
                    code="FACT_CONFIRMED_IMMUTABLE",
                )
            if existing.value == value and existing.status == proposed_status:
                return existing, False
            # Revisi: supersede existing, buat baris baru terlacak.
            existing.status = STATUS_SUPERSEDED
            new_fact = UserFact(
                id=new_ulid(),
                user_id=user_id,
                fact_key=fact_key,
                value=value,
                confidence=confidence,
                status=proposed_status,
                provenance_ref=provenance_ref,
                source_version=source_version,
                supersedes_id=existing.id,
            )
            self.session.add(new_fact)
            await self.session.flush()
            return new_fact, True

        fact = UserFact(
            id=new_ulid(),
            user_id=user_id,
            fact_key=fact_key,
            value=value,
            confidence=confidence,
            status=proposed_status,
            provenance_ref=provenance_ref,
            source_version=source_version,
        )
        self.session.add(fact)
        await self.session.flush()
        return fact, True

    async def confirm_fact(self, *, user_id: str, fact_id: str) -> UserFact:
        """Konfirmasi eksplisit user atas fact proposed."""
        fact = await self._owned_fact(user_id, fact_id)
        if fact.status == STATUS_CONFIRMED:
            return fact
        if fact.status != STATUS_PROPOSED:
            raise ConflictError(
                "Hanya fact proposed yang dapat dikonfirmasi.",
                code="FACT_NOT_CONFIRMABLE",
            )
        fact.status = STATUS_CONFIRMED
        await self.session.flush()
        return fact

    async def reject_fact(self, *, user_id: str, fact_id: str) -> UserFact:
        fact = await self._owned_fact(user_id, fact_id)
        if fact.status in (STATUS_SUPERSEDED, STATUS_REJECTED):
            return fact
        fact.status = STATUS_REJECTED
        await self.session.flush()
        return fact

    async def list_facts(self, *, user_id: str, limit: int = 50) -> list[UserFact]:
        rows = (
            (
                await self.session.execute(
                    select(UserFact)
                    .where(
                        UserFact.user_id == user_id,
                        UserFact.status.in_(ACTIVE_STATUSES),
                    )
                    .order_by(UserFact.created_at.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def _owned_fact(self, user_id: str, fact_id: str) -> UserFact:
        fact = (
            await self.session.execute(select(UserFact).where(UserFact.id == fact_id))
        ).scalar_one_or_none()
        if fact is None or fact.user_id != user_id:
            raise NotFoundError("Fact tidak ditemukan.")
        return fact
