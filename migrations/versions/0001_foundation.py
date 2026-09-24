"""FND-03: identity + reliability + execution grants schema

Revision ID: 0001_foundation
Revises:
Create Date: 2025-09-24

Schema sesuai .blueprint/postgresql-schema.md untuk scope foundation.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Identity ---
    op.create_table(
        "users",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("normalized_email", sa.String(320), nullable=False, unique=True),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("auth_epoch", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locale", sa.String(20), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('active','disabled','deleted')", name="ck_users_status"),
    )
    op.create_index("ix_users_normalized_email", "users", ["normalized_email"], unique=True)

    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("algorithm", sa.String(40), nullable=False, server_default="argon2id"),
        sa.Column("params_version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "auth_identities",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("provider", "subject", name="uq_auth_identities_provider_subject"),
    )
    op.create_index("ix_auth_identities_user_id", "auth_identities", ["user_id"])

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("family_id", sa.String(26), nullable=False),
        sa.Column("refresh_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("parent_id", sa.String(26), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_epoch", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_family_id", "auth_sessions", ["family_id"])

    op.create_table(
        "auth_action_tokens",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_auth_action_tokens_user_id", "auth_action_tokens", ["user_id"])

    op.create_table(
        "oauth_transactions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("nonce_hash", sa.String(64), nullable=False),
        sa.Column("encrypted_pkce_verifier", sa.Text, nullable=False),
        sa.Column("intent", sa.String(10), nullable=False),
        sa.Column("bound_user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=True),
        sa.Column("browser_binding_hash", sa.String(64), nullable=False),
        sa.Column("redirect_ref", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("intent IN ('login','link')", name="ck_oauth_intent"),
    )

    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("display_name", sa.String(120), nullable=True),
        sa.Column("english_level", sa.String(20), nullable=True),
        sa.Column("learning_goals", sa.Text, nullable=True),
        sa.Column("preferences", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    # --- Reliability ---
    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("principal", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(128), nullable=False),
        sa.Column("key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("principal", "operation", "key", name="uq_idempotency_principal_op_key"),
    )
    op.create_index("ix_idempotency_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(26), primary_key=True),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("aggregate_version", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_unpublished", "outbox_events", ["published_at"])

    op.create_table(
        "background_jobs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("dedupe_key", sa.String(255), nullable=False, unique=True),
        sa.Column("outbox_ref", sa.String(26), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fencing_token", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("checkpoint", sa.Text, nullable=True),
        sa.Column("safe_error", sa.Text, nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("payload", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "state IN ('pending','running','retry_scheduled','reconciliation_required','succeeded','failed','cancelled')",
            name="ck_background_jobs_state",
        ),
    )
    op.create_index("ix_background_jobs_state", "background_jobs", ["state"])

    op.create_table(
        "background_job_attempts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("job_id", sa.String(26), sa.ForeignKey("background_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("execution_grant_id", sa.String(26), nullable=True),
        sa.Column("runtime_snapshot_id", sa.String(26), nullable=True),
        sa.Column("vendor_job_id", sa.String(128), nullable=True),
        sa.Column("vendor", sa.String(40), nullable=True),
        sa.Column("vendor_version", sa.String(40), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint", sa.Text, nullable=True),
        sa.Column("safe_error", sa.Text, nullable=True),
        sa.UniqueConstraint("job_id", "attempt_number", name="uq_job_attempt"),
    )

    op.create_table(
        "tool_executions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("context_refs", sa.Text, nullable=True),
        sa.Column("result_ref", sa.Text, nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "tool_name", "idempotency_key", name="uq_tool_exec"),
    )
    op.create_index("ix_tool_executions_user_id", "tool_executions", ["user_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target", sa.String(255), nullable=True),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("metadata_redacted", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])
    op.create_index("ix_audit_events_created_at", "audit_events", ["created_at"])

    op.create_table(
        "deletion_requests",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("user_id", sa.String(26), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("tombstone_version", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_deletion_requests_user_id", "deletion_requests", ["user_id"])

    op.create_table(
        "bootstrap_runs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("environment", sa.String(40), nullable=False),
        sa.Column("manifest_version", sa.String(40), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("attempt_number", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("actor_ref", sa.String(128), nullable=True),
        sa.Column("migration_revision", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("change_summary", sa.Text, nullable=True),
        sa.Column("safe_error", sa.Text, nullable=True),
        sa.UniqueConstraint("environment", "manifest_version", "attempt_number", name="uq_bootstrap_run"),
    )

    # --- AI runtime: execution grants ---
    op.create_table(
        "execution_grants",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("request_id", sa.String(26), nullable=False),
        sa.Column("snapshot_id", sa.String(26), nullable=True),
        sa.Column("service_identity", sa.String(64), nullable=False),
        sa.Column("owner_user_id", sa.String(26), nullable=True),
        sa.Column("resource_ref", sa.String(255), nullable=True),
        sa.Column("purpose", sa.String(64), nullable=False),
        sa.Column("scopes", sa.Text, nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replay_policy", sa.String(20), nullable=False, server_default="single_use"),
    )
    op.create_index("ix_execution_grants_service_identity", "execution_grants", ["service_identity"])
    op.create_index("ix_execution_grants_owner_user_id", "execution_grants", ["owner_user_id"])
    op.create_index("ix_execution_grants_expiry", "execution_grants", ["expires_at"])

    # Immutable protections: audit_events dan outbox_events tidak dapat di-update/delete via trigger
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'append_only_table';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("audit_events",):
        op.execute(
            f"CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )
        op.execute(
            f"CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION prevent_mutation();"
        )


def downgrade() -> None:
    for table in ("audit_events",):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update ON {table};")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete ON {table};")
    op.execute("DROP FUNCTION IF EXISTS prevent_mutation();")
    op.drop_table("execution_grants")
    op.drop_table("bootstrap_runs")
    op.drop_table("deletion_requests")
    op.drop_table("audit_events")
    op.drop_table("tool_executions")
    op.drop_table("background_job_attempts")
    op.drop_table("background_jobs")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_records")
    op.drop_table("user_profiles")
    op.drop_table("oauth_transactions")
    op.drop_table("auth_action_tokens")
    op.drop_table("auth_sessions")
    op.drop_table("auth_identities")
    op.drop_table("password_credentials")
    op.drop_table("users")
