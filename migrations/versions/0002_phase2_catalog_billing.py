"""Phase 2: plans, catalog, agents, runtime registry, dan billing schema

Revision ID: 0002_phase2_catalog_billing
Revises: 0001_foundation
Create Date: 2025-09-24

Schema sesuai .blueprint/postgresql-schema.md untuk scope Phase 2
(Catalog, Agents, Plans dan Billing). Seed/provisioning mengikuti
.blueprint/database-bootstrap.md; migration ini tidak membuat seed,
pengguna, saldo, atau harga contoh.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase2_catalog_billing"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Plans ---
    op.create_table(
        "plans",
        sa.Column("code", sa.String(20), primary_key=True),
        sa.Column("policy_version", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("code IN ('vip','advance')", name="ck_plans_code"),
        sa.CheckConstraint("status IN ('draft','active','retired')", name="ck_plans_status"),
    )

    op.create_table(
        "plan_policy_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "plan_code",
            sa.String(20),
            sa.ForeignKey("plans.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("policy", sa.Text, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("plan_code", "revision", name="uq_plan_policy_revision"),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_plan_policy_status"
        ),
    )

    op.create_table(
        "user_plan_selections",
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "plan_code",
            sa.String(20),
            sa.ForeignKey("plans.code", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("revision", sa.Integer, nullable=True),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    # plans.policy_version harus mengacu revision milik plan yang sama (composite FK)
    op.create_foreign_key(
        "fk_plans_policy_version",
        "plans",
        "plan_policy_versions",
        ["code", "policy_version"],
        ["plan_code", "revision"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_user_plan_policy_version",
        "user_plan_selections",
        "plan_policy_versions",
        ["plan_code", "revision"],
        ["plan_code", "revision"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "plan_change_events",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("old_plan_code", sa.String(20), nullable=True),
        sa.Column("new_plan_code", sa.String(20), nullable=False),
        sa.Column("revision", sa.Integer, nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_plan_change_events_user_id", "plan_change_events", ["user_id"])

    # --- Provider & model catalog ---
    op.create_table(
        "provider_catalog",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column("canonical_base_url", sa.Text, nullable=True),
        sa.Column("allows_custom_base_url", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("base_url_policy", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('staged','active','disabled')", name="ck_provider_status"),
    )

    op.create_table(
        "ai_model_configurations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "provider_id",
            sa.String(26),
            sa.ForeignKey("provider_catalog.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("identifier", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("capabilities", sa.Text, nullable=False),
        sa.Column("adapter", sa.String(64), nullable=False),
        sa.Column("adapter_version", sa.String(40), nullable=False),
        sa.Column("metering_contract", sa.Text, nullable=True),
        sa.Column("limits", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "provider_id", "identifier", "revision", name="uq_model_provider_identifier_revision"
        ),
        sa.CheckConstraint(
            "status IN ('staged','active','disabled')", name="ck_model_config_status"
        ),
    )
    op.create_index(
        "ix_ai_model_configurations_provider_id", "ai_model_configurations", ["provider_id"]
    )

    # --- BYOK credentials & selections ---
    op.create_table(
        "user_ai_credentials",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "provider_id",
            sa.String(26),
            sa.ForeignKey("provider_catalog.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("encrypted_api_key", sa.Text, nullable=False),
        sa.Column("encrypted_base_url", sa.Text, nullable=True),
        sa.Column("key_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_verification"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('pending_verification','active','revoked','verification_failed')",
            name="ck_user_ai_credentials_status",
        ),
    )
    op.create_index("ix_user_ai_credentials_user_id", "user_ai_credentials", ["user_id"])
    op.create_index("ix_user_ai_credentials_provider_id", "user_ai_credentials", ["provider_id"])

    op.create_table(
        "user_ai_selections",
        sa.Column("user_id", sa.String(26), nullable=False),
        sa.Column("capability", sa.String(20), nullable=False),
        sa.Column(
            "credential_id",
            sa.String(26),
            sa.ForeignKey("user_ai_credentials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(26),
            sa.ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("user_id", "capability", name="pk_user_ai_selections"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint("capability IN ('llm','stt')", name="ck_user_ai_selections_capability"),
    )
    op.create_index("ix_user_ai_selections_credential_id", "user_ai_selections", ["credential_id"])
    op.create_index("ix_user_ai_selections_model_id", "user_ai_selections", ["model_id"])

    # --- Agents & voice ---
    op.create_table(
        "agents",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("active_version_id", sa.String(26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("code IN ('elean','willy')", name="ck_agents_code"),
        sa.CheckConstraint("status IN ('draft','active','retired')", name="ck_agents_status"),
    )

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(26),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("persona_artifact_ref", sa.Text, nullable=True),
        sa.Column("persona_artifact_hash", sa.String(64), nullable=True),
        sa.Column("voice_binding_ref", sa.Text, nullable=True),
        sa.Column("knowledge_version", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("agent_id", "revision", name="uq_agent_version_revision"),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_agent_versions_status"
        ),
    )
    op.create_index("ix_agent_versions_agent_id", "agent_versions", ["agent_id"])
    op.create_foreign_key(
        "fk_agents_active_version",
        "agents",
        "agent_versions",
        ["active_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "voice_configuration_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "agent_id",
            sa.String(26),
            sa.ForeignKey("agents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model_identifier", sa.String(128), nullable=False),
        sa.Column("voice_identifier", sa.String(128), nullable=False),
        sa.Column("settings", sa.Text, nullable=True),
        sa.Column("settings_hash", sa.String(64), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("agent_id", "revision", name="uq_voice_config_revision"),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_voice_config_status"
        ),
    )
    op.create_index(
        "ix_voice_configuration_versions_agent_id", "voice_configuration_versions", ["agent_id"]
    )

    # --- Flow & tool registries ---
    op.create_table(
        "ai_flow_registry",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("flow_version", sa.String(40), nullable=False),
        sa.Column("langflow_flow_id", sa.String(128), nullable=False),
        sa.Column("input_schema_version", sa.String(40), nullable=False),
        sa.Column("output_schema_version", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=True),
        sa.Column("required_capabilities", sa.Text, nullable=True),
        sa.Column("tool_allowlist", sa.Text, nullable=True),
        sa.Column("timeout_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "environment", "purpose", "flow_version", name="uq_flow_registry_env_purpose_version"
        ),
        sa.CheckConstraint(
            "status IN ('staged','active','disabled')", name="ck_flow_registry_status"
        ),
    )
    # Satu flow active per environment/purpose
    op.execute(
        "CREATE UNIQUE INDEX uq_flow_registry_one_active "
        "ON ai_flow_registry (environment, purpose) WHERE status = 'active';"
    )

    op.create_table(
        "tool_registry",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("callcraft_spec_id", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("idempotency_policy", sa.String(40), nullable=False),
        sa.Column("timeout_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="staged"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "environment", "name", "schema_version", name="uq_tool_registry_env_name_version"
        ),
        sa.CheckConstraint(
            "status IN ('staged','active','disabled')", name="ck_tool_registry_status"
        ),
    )

    # --- Runtime snapshots (immutable) ---
    op.create_table(
        "runtime_snapshots",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("principal_kind", sa.String(20), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("service_principal_ref", sa.String(128), nullable=True),
        sa.Column(
            "plan_policy_version_id",
            sa.String(26),
            sa.ForeignKey("plan_policy_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "llm_model_configuration_id",
            sa.String(26),
            sa.ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "stt_model_configuration_id",
            sa.String(26),
            sa.ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("credential_record_ref", sa.String(26), nullable=True),
        sa.Column(
            "agent_version_id",
            sa.String(26),
            sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "voice_configuration_version_id",
            sa.String(26),
            sa.ForeignKey("voice_configuration_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("prompt_version", sa.String(40), nullable=True),
        sa.Column("flow_version_ref", sa.String(26), nullable=True),
        sa.Column("rate_card_version_id", sa.String(26), nullable=True),
        sa.Column("policy", sa.Text, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "principal_kind IN ('user','service')", name="ck_runtime_snapshots_principal_kind"
        ),
        sa.CheckConstraint(
            "(principal_kind = 'user' AND owner_user_id IS NOT NULL) OR "
            "(principal_kind = 'service' AND service_principal_ref IS NOT NULL)",
            name="ck_runtime_snapshots_principal_scope",
        ),
    )
    op.create_index("ix_runtime_snapshots_owner_user_id", "runtime_snapshots", ["owner_user_id"])

    # --- Billing: packages & rate cards ---
    op.create_table(
        "token_package_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("package_code", sa.String(40), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("token_units", sa.BigInteger, nullable=False),
        sa.Column("display_scale", sa.Integer, nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("package_code", "revision", name="uq_token_package_revision"),
        sa.CheckConstraint("amount_minor > 0", name="ck_token_package_amount_positive"),
        sa.CheckConstraint("token_units > 0", name="ck_token_package_units_positive"),
        sa.CheckConstraint(
            "status IN ('draft','published','retired')", name="ck_token_package_status"
        ),
    )

    op.create_table(
        "rate_card_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("version", sa.String(40), nullable=False, unique=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rounding_policy", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("status IN ('draft','published','retired')", name="ck_rate_card_status"),
    )

    op.create_table(
        "rate_card_items",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "rate_card_id",
            sa.String(26),
            sa.ForeignKey("rate_card_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_id",
            sa.String(26),
            sa.ForeignKey("ai_model_configurations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(20), nullable=False),
        sa.Column("meter", sa.String(40), nullable=False),
        sa.Column("quantity_unit", sa.String(20), nullable=False),
        sa.Column("cost_numerator", sa.BigInteger, nullable=False),
        sa.Column("cost_denominator", sa.BigInteger, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "rate_card_id", "model_id", "capability", "meter", name="uq_rate_card_item"
        ),
        sa.CheckConstraint("cost_numerator >= 0", name="ck_rate_card_item_numerator"),
        sa.CheckConstraint("cost_denominator > 0", name="ck_rate_card_item_denominator"),
    )
    op.create_index("ix_rate_card_items_rate_card_id", "rate_card_items", ["rate_card_id"])
    op.create_foreign_key(
        "fk_runtime_snapshots_rate_card",
        "runtime_snapshots",
        "rate_card_versions",
        ["rate_card_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    # --- Billing: wallets & ledger (append-only) ---
    op.create_table(
        "wallets",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column("available_units", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("held_units", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("version", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("available_units >= 0", name="ck_wallets_available_nonnegative"),
        sa.CheckConstraint("held_units >= 0", name="ck_wallets_held_nonnegative"),
    )

    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.String(26),
            sa.ForeignKey("wallets.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("account_type", sa.String(40), nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("wallet_id", "account_type", "asset", name="uq_ledger_account_logical"),
    )

    op.create_table(
        "ledger_journals",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("business_key", sa.String(255), nullable=False, unique=True),
        sa.Column("operation_ref", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("reversal_of", sa.String(26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["reversal_of"], ["ledger_journals.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "journal_id",
            sa.String(26),
            sa.ForeignKey("ledger_journals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "account_id",
            sa.String(26),
            sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("signed_units", sa.BigInteger, nullable=False),
        sa.Column("asset", sa.String(20), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("signed_units <> 0", name="ck_ledger_entries_nonzero"),
    )
    op.create_index("ix_ledger_entries_journal_id", "ledger_entries", ["journal_id"])
    op.create_index("ix_ledger_entries_account_id", "ledger_entries", ["account_id"])

    # --- Billing: reservations, invocations, usage, settlements ---
    op.create_table(
        "usage_reservations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "wallet_id",
            sa.String(26),
            sa.ForeignKey("wallets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "rate_card_id",
            sa.String(26),
            sa.ForeignKey("rate_card_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("authorized_units", sa.BigInteger, nullable=False),
        sa.Column("settled_units", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("released_units", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("state", sa.String(32), nullable=False, server_default="active"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("authorized_units > 0", name="ck_reservation_authorized_positive"),
        sa.CheckConstraint(
            "settled_units >= 0 AND released_units >= 0", name="ck_reservation_parts_nonnegative"
        ),
        sa.CheckConstraint(
            "settled_units + released_units <= authorized_units",
            name="ck_reservation_within_authorized",
        ),
        sa.CheckConstraint(
            "state IN ('active','settling','settled','released','reconciliation_required')",
            name="ck_reservation_state",
        ),
    )
    op.create_index("ix_usage_reservations_wallet_id", "usage_reservations", ["wallet_id"])
    op.create_index("ix_usage_reservations_state", "usage_reservations", ["state"])

    op.create_table(
        "ai_invocations",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("operation_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "runtime_snapshot_id",
            sa.String(26),
            sa.ForeignKey("runtime_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("capability", sa.String(20), nullable=False),
        sa.Column("payer", sa.String(20), nullable=False),
        sa.Column("provider_request_id", sa.String(128), nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="started"),
        sa.Column(
            "reservation_id",
            sa.String(26),
            sa.ForeignKey("usage_reservations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("payer IN ('user','platform')", name="ck_ai_invocations_payer"),
        sa.CheckConstraint(
            "state IN ('started','completed','failed','cancelled','reconciliation_required')",
            name="ck_ai_invocations_state",
        ),
    )
    op.create_index("ix_ai_invocations_snapshot_id", "ai_invocations", ["runtime_snapshot_id"])
    op.create_index("ix_ai_invocations_reservation_id", "ai_invocations", ["reservation_id"])

    op.create_table(
        "usage_records",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "invocation_id",
            sa.String(26),
            sa.ForeignKey("ai_invocations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("meter", sa.String(40), nullable=False),
        sa.Column("meter_version", sa.String(40), nullable=False),
        sa.Column("cumulative_quantity", sa.BigInteger, nullable=False),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("provider_evidence_ref", sa.Text, nullable=True),
        sa.Column("final", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("invocation_id", "meter", "sequence", name="uq_usage_record_sequence"),
        sa.CheckConstraint(
            "cumulative_quantity >= 0", name="ck_usage_records_quantity_nonnegative"
        ),
    )
    op.create_index("ix_usage_records_invocation_id", "usage_records", ["invocation_id"])

    op.create_table(
        "usage_settlements",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "invocation_id",
            sa.String(26),
            sa.ForeignKey("ai_invocations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("cumulative_charge", sa.BigInteger, nullable=False),
        sa.Column(
            "journal_id",
            sa.String(26),
            sa.ForeignKey("ledger_journals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rate_card_id",
            sa.String(26),
            sa.ForeignKey("rate_card_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("invocation_id", "revision", name="uq_usage_settlement_revision"),
        sa.CheckConstraint(
            "cumulative_charge >= 0", name="ck_usage_settlements_charge_nonnegative"
        ),
    )
    op.create_index("ix_usage_settlements_journal_id", "usage_settlements", ["journal_id"])

    # --- Billing: payments ---
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "package_version_id",
            sa.String(26),
            sa.ForeignKey("token_package_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("merchant_reference", sa.String(128), nullable=False, unique=True),
        sa.Column("provider_payment_id", sa.String(128), nullable=True, unique=True),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("token_units", sa.BigInteger, nullable=False),
        sa.Column("checkout_url", sa.Text, nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="created"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_orders_amount_positive"),
        sa.CheckConstraint("token_units > 0", name="ck_payment_orders_units_positive"),
        sa.CheckConstraint(
            "state IN ('created','pending','paid','failed','expired','refunded','disputed')",
            name="ck_payment_orders_state",
        ),
    )
    op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"])
    op.create_index("ix_payment_orders_state", "payment_orders", ["state"])

    op.create_table(
        "webhook_inbox",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("provider_event_key", sa.String(255), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("payload_ref", sa.Text, nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="received"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider", "provider_event_key", name="uq_webhook_inbox_event"),
        sa.CheckConstraint(
            "state IN ('received','processing','processed','failed','ignored')",
            name="ck_webhook_inbox_state",
        ),
    )
    op.create_index("ix_webhook_inbox_state", "webhook_inbox", ["state"])

    op.create_table(
        "payment_refunds",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(26),
            sa.ForeignKey("payment_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_refund_id", sa.String(128), nullable=True, unique=True),
        sa.Column("request_key", sa.String(128), nullable=False, unique=True),
        sa.Column("amount_minor", sa.BigInteger, nullable=False),
        sa.Column("token_units", sa.BigInteger, nullable=False),
        sa.Column(
            "journal_id",
            sa.String(26),
            sa.ForeignKey("ledger_journals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("state", sa.String(32), nullable=False, server_default="requested"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_refunds_amount_positive"),
        sa.CheckConstraint("token_units > 0", name="ck_payment_refunds_units_positive"),
        sa.CheckConstraint(
            "state IN ('requested','processing','succeeded','failed')",
            name="ck_payment_refunds_state",
        ),
    )
    op.create_index("ix_payment_refunds_order_id", "payment_refunds", ["order_id"])

    op.create_table(
        "payment_disputes",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "order_id",
            sa.String(26),
            sa.ForeignKey("payment_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_dispute_ref", sa.String(128), nullable=False, unique=True),
        sa.Column("outstanding_units", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("evidence_ref", sa.Text, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "status IN ('open','won','lost','closed')", name="ck_payment_disputes_status"
        ),
    )
    op.create_index("ix_payment_disputes_order_id", "payment_disputes", ["order_id"])

    # --- Immutable protections untuk append-only billing tables ---
    for table in (
        "ledger_journals",
        "ledger_entries",
        "usage_records",
        "usage_settlements",
        "plan_change_events",
    ):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )


def downgrade() -> None:
    for table in (
        "ledger_journals",
        "ledger_entries",
        "usage_records",
        "usage_settlements",
        "plan_change_events",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table};")

    op.drop_table("payment_disputes")
    op.drop_table("payment_refunds")
    op.drop_table("webhook_inbox")
    op.drop_table("payment_orders")
    op.drop_table("usage_settlements")
    op.drop_table("usage_records")
    op.drop_table("ai_invocations")
    op.drop_table("usage_reservations")
    op.drop_table("ledger_entries")
    op.drop_table("ledger_journals")
    op.drop_table("ledger_accounts")
    op.drop_table("wallets")
    op.drop_table("runtime_snapshots")
    op.drop_table("rate_card_items")
    op.drop_table("rate_card_versions")
    op.drop_table("token_package_versions")
    op.drop_table("tool_registry")
    op.execute("DROP INDEX IF EXISTS uq_flow_registry_one_active;")
    op.drop_table("ai_flow_registry")
    op.drop_table("voice_configuration_versions")
    op.drop_constraint("fk_agents_active_version", "agents", type_="foreignkey")
    op.drop_table("agent_versions")
    op.drop_table("agents")
    op.drop_table("user_ai_selections")
    op.drop_table("user_ai_credentials")
    op.drop_table("ai_model_configurations")
    op.drop_table("provider_catalog")
    op.drop_table("plan_change_events")
    op.drop_constraint("fk_user_plan_policy_version", "user_plan_selections", type_="foreignkey")
    op.drop_table("user_plan_selections")
    op.drop_constraint("fk_plans_policy_version", "plans", type_="foreignkey")
    op.drop_table("plan_policy_versions")
    op.drop_table("plans")
