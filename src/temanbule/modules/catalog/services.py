"""Catalog service: plans, providers, models, agents, runtime snapshots (Phase 2).

Aturan (billing-plans.md, postgresql-schema.md, database-bootstrap.md):
- Provider/model aktif dipilih dari DB, bukan string bebas user.
- Published versions immutable; perubahan lewat revision baru.
- Snapshot immutable, tanpa plaintext secrets; credential reference sekali
  pakai dibuat per attempt, tidak disimpan di snapshot.
- Aktivasi Advance memerlukan selection LLM/STT aktif + credential valid;
  tidak ada fallback ke key admin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from temanbule.modules.catalog.models import (
    Agent,
    AgentVersion,
    AiModelConfiguration,
    Plan,
    PlanPolicyVersion,
    ProviderCatalog,
    RuntimeSnapshot,
    UserAiCredential,
    UserAiSelection,
    UserPlanSelection,
)
from temanbule.platform.errors import ConflictError, NotFoundError
from temanbule.platform.security import new_ulid

PLAN_VIP = "vip"
PLAN_ADVANCE = "advance"

CAPABILITY_LLM = "llm"
CAPABILITY_STT = "stt"

CREDENTIAL_ACTIVE = "active"


class CatalogService:
    """Read-path untuk entitas katalog aktif/published."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_plan(self, code: str) -> Plan:
        plan = (
            await self.session.execute(
                select(Plan).where(Plan.code == code, Plan.status == "active")
            )
        ).scalar_one_or_none()
        if plan is None:
            raise NotFoundError(f"Plan '{code}' tidak aktif.")
        return plan

    async def get_published_policy(self, plan_code: str, revision: int) -> PlanPolicyVersion:
        policy = (
            await self.session.execute(
                select(PlanPolicyVersion).where(
                    PlanPolicyVersion.plan_code == plan_code,
                    PlanPolicyVersion.revision == revision,
                    PlanPolicyVersion.status == "published",
                )
            )
        ).scalar_one_or_none()
        if policy is None:
            raise NotFoundError("Policy version tidak ditemukan atau belum published.")
        return policy

    async def get_active_model(self, model_id: str) -> AiModelConfiguration:
        model = (
            await self.session.execute(
                select(AiModelConfiguration).where(
                    AiModelConfiguration.id == model_id,
                    AiModelConfiguration.status == "active",
                )
            )
        ).scalar_one_or_none()
        if model is None:
            raise NotFoundError("Model configuration tidak aktif.")
        provider = (
            await self.session.execute(
                select(ProviderCatalog).where(
                    ProviderCatalog.id == model.provider_id,
                    ProviderCatalog.status == "active",
                )
            )
        ).scalar_one_or_none()
        if provider is None:
            raise ConflictError(
                "Provider model tidak aktif.",
                code="PROVIDER_INACTIVE",
            )
        return model

    async def get_active_agent_version(self, agent_code: str) -> AgentVersion:
        agent = (
            await self.session.execute(
                select(Agent).where(Agent.code == agent_code, Agent.status == "active")
            )
        ).scalar_one_or_none()
        if agent is None or agent.active_version_id is None:
            raise NotFoundError(f"Agent '{agent_code}' tidak punya versi aktif.")
        version = (
            await self.session.execute(
                select(AgentVersion).where(
                    AgentVersion.id == agent.active_version_id,
                    AgentVersion.status == "published",
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise ConflictError(
                "Active version agent tidak published.",
                code="AGENT_VERSION_INVALID",
            )
        return version


@dataclass(frozen=True)
class SnapshotRequest:
    principal_kind: str  # user | service
    owner_user_id: str | None
    service_principal_ref: str | None
    plan_policy_version_id: str | None
    llm_model_configuration_id: str | None
    stt_model_configuration_id: str | None
    agent_version_id: str | None
    policy_json: str


class RuntimeSnapshotBuilder:
    """Membangun immutable runtime snapshot. Tidak menyimpan secrets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.catalog = CatalogService(session)

    async def build_for_user(self, *, user_id: str, agent_code: str) -> RuntimeSnapshot:
        """Snapshot user-facing: wajib plan selection + agent version.

        Advance: credential LLM/STT aktif wajib; VIP: platform key (policy).
        Gagal eksplisit bila syarat plan tidak terpenuhi — tanpa fallback.
        """
        selection = (
            await self.session.execute(
                select(UserPlanSelection).where(UserPlanSelection.user_id == user_id)
            )
        ).scalar_one_or_none()
        if selection is None or selection.plan_code is None or selection.revision is None:
            raise ConflictError(
                "User belum memilih plan.",
                code="PLAN_NOT_SELECTED",
            )
        policy = await self.catalog.get_published_policy(selection.plan_code, selection.revision)
        agent_version = await self.catalog.get_active_agent_version(agent_code)

        llm_model_id: str | None = None
        stt_model_id: str | None = None
        if selection.plan_code == PLAN_ADVANCE:
            llm_model_id = await self._selected_model(user_id, CAPABILITY_LLM)
            stt_model_id = await self._selected_model(user_id, CAPABILITY_STT)

        snapshot = RuntimeSnapshot(
            id=new_ulid(),
            principal_kind="user",
            owner_user_id=user_id,
            plan_policy_version_id=policy.id,
            llm_model_configuration_id=llm_model_id,
            stt_model_configuration_id=stt_model_id,
            agent_version_id=agent_version.id,
            policy=policy.policy,
            created_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def _selected_model(self, user_id: str, capability: str) -> str:
        selection = (
            await self.session.execute(
                select(UserAiSelection).where(
                    UserAiSelection.user_id == user_id,
                    UserAiSelection.capability == capability,
                )
            )
        ).scalar_one_or_none()
        if selection is None:
            raise ConflictError(
                f"Plan Advance memerlukan selection {capability} aktif.",
                code="BYOK_SELECTION_MISSING",
                details=[{"field": "capability", "message": capability}],
            )
        credential = (
            await self.session.execute(
                select(UserAiCredential).where(
                    UserAiCredential.id == selection.credential_id,
                    UserAiCredential.user_id == user_id,
                    UserAiCredential.status == CREDENTIAL_ACTIVE,
                    UserAiCredential.revoked_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if credential is None:
            raise ConflictError(
                "Credential BYOK tidak aktif atau sudah direvoke.",
                code="BYOK_CREDENTIAL_INVALID",
            )
        model = await self.catalog.get_active_model(selection.model_id)
        if model.provider_id != credential.provider_id:
            raise ConflictError(
                "Credential dan model bukan provider yang sama.",
                code="BYOK_PROVIDER_MISMATCH",
            )
        return model.id
