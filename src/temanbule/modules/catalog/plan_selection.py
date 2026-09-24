"""Plan selection & switching service (Phase 2).

Kontrak (billing-plans.md "Plan Switching"):
- PUT /v1/me/plan optimistic concurrency; 409 bila ada invocation user-plan
  belum terminal/settled/reconciled.
- Aktivasi Advance memerlukan selection LLM/STT aktif + credential valid.
- Saldo VIP dan credential Advance tetap ada setelah pindah plan.
- Perpindahan dicatat append-only di plan_change_events.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.billing.models import AiInvocation, UsageReservation
from temanbule.modules.catalog.models import (
    Plan,
    PlanChangeEvent,
    PlanPolicyVersion,
    UserPlanSelection,
)
from temanbule.modules.catalog.services import CatalogService
from temanbule.platform.errors import ConflictError, NotFoundError
from temanbule.platform.security import new_ulid

TERMINAL_INVOCATION_STATES = ("completed", "failed", "cancelled")
OPEN_RESERVATION_STATES = ("active", "settling", "reconciliation_required")


class PlanSelectionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.catalog = CatalogService(session)

    async def select_plan(
        self, *, user_id: str, plan_code: str, expected_revision: int | None = None
    ) -> UserPlanSelection:
        plan = (
            await self.session.execute(
                select(Plan).where(Plan.code == plan_code, Plan.status == "active")
            )
        ).scalar_one_or_none()
        if plan is None or plan.policy_version is None:
            raise NotFoundError("Plan tidak tersedia.")
        policy = (
            await self.session.execute(
                select(PlanPolicyVersion).where(
                    PlanPolicyVersion.plan_code == plan.code,
                    PlanPolicyVersion.revision == plan.policy_version,
                    PlanPolicyVersion.status == "published",
                )
            )
        ).scalar_one_or_none()
        if policy is None:
            raise ConflictError(
                "Plan belum punya published policy.",
                code="PLAN_POLICY_MISSING",
            )

        current = (
            await self.session.execute(
                select(UserPlanSelection)
                .where(UserPlanSelection.user_id == user_id)
                .with_for_update()
            )
        ).scalar_one_or_none()

        # Optimistic concurrency: caller wajib tahu revision yang sedang dipakai.
        if expected_revision is not None:
            current_revision = current.revision if current is not None else None
            if current_revision != expected_revision:
                raise ConflictError(
                    "Plan sudah berubah; muat ulang sebelum mengubah.",
                    code="PLAN_VERSION_CONFLICT",
                )

        if current is not None and current.plan_code == plan_code:
            return current  # no-op idempoten

        if current is not None and current.plan_code is not None:
            await self._assert_no_open_billing_work(user_id)

        if plan_code == "advance":
            await self._assert_advance_ready(user_id)

        old_plan = current.plan_code if current is not None else None
        now = datetime.now(UTC)
        if current is None:
            current = UserPlanSelection(
                user_id=user_id,
                plan_code=plan.code,
                revision=policy.revision,
                selected_at=now,
            )
            self.session.add(current)
        else:
            current.plan_code = plan.code
            current.revision = policy.revision
            current.selected_at = now
        self.session.add(
            PlanChangeEvent(
                id=new_ulid(),
                user_id=user_id,
                old_plan_code=old_plan,
                new_plan_code=plan.code,
                revision=policy.revision,
            )
        )
        await self.session.flush()
        return current

    async def _assert_no_open_billing_work(self, user_id: str) -> None:
        """Blok 409 bila user masih punya invocation belum terminal atau
        reservation masih terbuka (active/settling/reconciliation)."""
        from temanbule.modules.billing.models import Wallet

        open_work = (
            await self.session.execute(
                select(func.count(AiInvocation.id))
                .select_from(AiInvocation)
                .join(UsageReservation, AiInvocation.reservation_id == UsageReservation.id)
                .join(Wallet, UsageReservation.wallet_id == Wallet.id)
                .where(
                    Wallet.user_id == user_id,
                    AiInvocation.payer == "user",
                    AiInvocation.state.not_in(TERMINAL_INVOCATION_STATES),
                )
            )
        ).scalar_one()
        if open_work > 0:
            raise ConflictError(
                "Masih ada pekerjaan berbayar yang belum selesai.",
                code="PLAN_SWITCH_BLOCKED",
            )
        open_reservations = (
            await self.session.execute(
                select(func.count(UsageReservation.id))
                .select_from(UsageReservation)
                .join(Wallet, UsageReservation.wallet_id == Wallet.id)
                .where(
                    Wallet.user_id == user_id,
                    UsageReservation.state.in_(OPEN_RESERVATION_STATES),
                )
            )
        ).scalar_one()
        if open_reservations > 0:
            raise ConflictError(
                "Masih ada reservation saldo yang belum selesai.",
                code="PLAN_SWITCH_BLOCKED",
            )

    async def _assert_advance_ready(self, user_id: str) -> None:
        """Advance wajib selection LLM & STT aktif (tanpa fallback admin key)."""
        from temanbule.modules.catalog.models import UserAiCredential, UserAiSelection

        rows = (
            (
                await self.session.execute(
                    select(UserAiSelection.capability)
                    .join(
                        UserAiCredential,
                        UserAiSelection.credential_id == UserAiCredential.id,
                    )
                    .where(
                        UserAiSelection.user_id == user_id,
                        UserAiCredential.status == "active",
                        UserAiCredential.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        missing = {"llm", "stt"} - set(rows)
        if missing:
            raise ConflictError(
                "Aktivasi Advance memerlukan selection LLM dan STT aktif.",
                code="BYOK_SELECTION_MISSING",
                details=[{"field": "capability", "message": m} for m in sorted(missing)],
            )
