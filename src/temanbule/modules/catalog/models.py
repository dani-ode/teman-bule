"""Catalog module models: plans, providers, models, agents, registries (Phase 2).

Sesuai .blueprint/postgresql-schema.md. Published versions bersifat immutable
oleh konvensi aplikasi; tidak ada UPDATE pada baris published.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from temanbule.platform.base import Base, TimestampMixin, utcnow


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)  # vip | advance
    policy_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|active|retired

    policy_versions: Mapped[list[PlanPolicyVersion]] = relationship(back_populates="plan")


class PlanPolicyVersion(Base):
    """Policy terversi; published immutable."""

    __tablename__ = "plan_policy_versions"
    __table_args__ = (
        UniqueConstraint("plan_code", "revision", name="uq_plan_policy_revision"),
        CheckConstraint("status IN ('draft','published','retired')", name="ck_plan_policy_status"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    plan_code: Mapped[str] = mapped_column(ForeignKey("plans.code", ondelete="RESTRICT"))
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    plan: Mapped[Plan] = relationship(back_populates="policy_versions")


class UserPlanSelection(Base, TimestampMixin):
    __tablename__ = "user_plan_selections"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_code", "revision"],
            ["plan_policy_versions.plan_code", "plan_policy_versions.revision"],
            ondelete="RESTRICT",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    plan_code: Mapped[str | None] = mapped_column(ForeignKey("plans.code", ondelete="RESTRICT"))
    revision: Mapped[int | None] = mapped_column(Integer)
    selected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanChangeEvent(Base):
    """Append-only; trigger mencegah UPDATE/DELETE."""

    __tablename__ = "plan_change_events"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    old_plan_code: Mapped[str | None] = mapped_column(String(20))
    new_plan_code: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderCatalog(Base, TimestampMixin):
    __tablename__ = "provider_catalog"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="staged")  # staged|active|disabled
    canonical_base_url: Mapped[str | None] = mapped_column(Text)
    allows_custom_base_url: Mapped[bool] = mapped_column(Boolean, default=False)
    base_url_policy: Mapped[str | None] = mapped_column(Text)

    models: Mapped[list[AiModelConfiguration]] = relationship(back_populates="provider")


class AiModelConfiguration(Base, TimestampMixin):
    __tablename__ = "ai_model_configurations"
    __table_args__ = (
        UniqueConstraint(
            "provider_id", "identifier", "revision", name="uq_model_provider_identifier_revision"
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_catalog.id", ondelete="RESTRICT"), index=True
    )
    identifier: Mapped[str] = mapped_column(String(128))
    revision: Mapped[int] = mapped_column(Integer)
    capabilities: Mapped[str] = mapped_column(Text)
    adapter: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(40))
    metering_contract: Mapped[str | None] = mapped_column(Text)
    limits: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="staged")

    provider: Mapped[ProviderCatalog] = relationship(back_populates="models")


class UserAiCredential(Base, TimestampMixin):
    """BYOK credential terenkripsi; tidak pernah menyimpan plaintext key."""

    __tablename__ = "user_ai_credentials"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[str] = mapped_column(
        ForeignKey("provider_catalog.id", ondelete="RESTRICT"), index=True
    )
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    encrypted_base_url: Mapped[str | None] = mapped_column(Text)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending_verification")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserAiSelection(Base, TimestampMixin):
    __tablename__ = "user_ai_selections"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(20), primary_key=True)  # llm | stt
    credential_id: Mapped[str] = mapped_column(
        ForeignKey("user_ai_credentials.id", ondelete="RESTRICT"), index=True
    )
    model_id: Mapped[str] = mapped_column(
        ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"), index=True
    )


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # elean | willy
    display_name: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    active_version_id: Mapped[str | None] = mapped_column(String(26))

    versions: Mapped[list[AgentVersion]] = relationship(
        back_populates="agent", foreign_keys="AgentVersion.agent_id"
    )


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "revision", name="uq_agent_version_revision"),
        ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(26), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    persona_artifact_ref: Mapped[str | None] = mapped_column(Text)
    persona_artifact_hash: Mapped[str | None] = mapped_column(String(64))
    voice_binding_ref: Mapped[str | None] = mapped_column(Text)
    knowledge_version: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    agent: Mapped[Agent] = relationship(back_populates="versions", foreign_keys=[agent_id])


class VoiceConfigurationVersion(Base):
    __tablename__ = "voice_configuration_versions"
    __table_args__ = (UniqueConstraint("agent_id", "revision", name="uq_voice_config_revision"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="RESTRICT"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(40))
    model_identifier: Mapped[str] = mapped_column(String(128))
    voice_identifier: Mapped[str] = mapped_column(String(128))
    settings: Mapped[str | None] = mapped_column(Text)
    settings_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AiFlowRegistry(Base, TimestampMixin):
    __tablename__ = "ai_flow_registry"
    __table_args__ = (
        UniqueConstraint(
            "environment", "purpose", "flow_version", name="uq_flow_registry_env_purpose_version"
        ),
        Index(
            "uq_flow_registry_one_active",
            "environment",
            "purpose",
            unique=True,
            postgresql_where="status = 'active'",
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    environment: Mapped[str] = mapped_column(String(40))
    purpose: Mapped[str] = mapped_column(String(64))
    flow_version: Mapped[str] = mapped_column(String(40))
    langflow_flow_id: Mapped[str] = mapped_column(String(128))
    input_schema_version: Mapped[str] = mapped_column(String(40))
    output_schema_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    required_capabilities: Mapped[str | None] = mapped_column(Text)
    tool_allowlist: Mapped[str | None] = mapped_column(Text)
    timeout_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="staged")


class ToolRegistry(Base, TimestampMixin):
    __tablename__ = "tool_registry"
    __table_args__ = (
        UniqueConstraint(
            "environment", "name", "schema_version", name="uq_tool_registry_env_name_version"
        ),
    )

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    environment: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(128))
    schema_version: Mapped[str] = mapped_column(String(40))
    callcraft_spec_id: Mapped[str] = mapped_column(String(128))
    scope: Mapped[str] = mapped_column(String(64))
    idempotency_policy: Mapped[str] = mapped_column(String(40))
    timeout_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="staged")


class RuntimeSnapshot(Base):
    """Immutable snapshot konfigurasi runtime; tidak ada plaintext secrets."""

    __tablename__ = "runtime_snapshots"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    principal_kind: Mapped[str] = mapped_column(String(20))  # user | service
    owner_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    service_principal_ref: Mapped[str | None] = mapped_column(String(128))
    plan_policy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_policy_versions.id", ondelete="RESTRICT")
    )
    llm_model_configuration_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_model_configurations.id", ondelete="RESTRICT")
    )
    stt_model_configuration_id: Mapped[str | None] = mapped_column(
        ForeignKey("ai_model_configurations.id", ondelete="RESTRICT")
    )
    credential_record_ref: Mapped[str | None] = mapped_column(String(26))
    agent_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="RESTRICT")
    )
    voice_configuration_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("voice_configuration_versions.id", ondelete="RESTRICT")
    )
    prompt_version: Mapped[str | None] = mapped_column(String(40))
    flow_version_ref: Mapped[str | None] = mapped_column(String(26))
    rate_card_version_id: Mapped[str | None] = mapped_column(String(26))
    policy: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
