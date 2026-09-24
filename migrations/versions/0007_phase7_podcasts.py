"""Phase 7: podcast schema

Revision ID: 0007_phase7_podcasts
Revises: 0006_phase6_calls
Create Date: 2025-09-24

Sesuai .blueprint/postgresql-schema.md bagian Podcast dan Knowledge.
Script versions ready bersifat immutable; playbacks punya lease/fencing
dan deadline untuk interruption branches.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_phase7_podcasts"
down_revision = "0006_phase6_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "podcasts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "user_id", sa.String(26), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="created"),
        sa.Column("current_source_version_id", sa.String(26), nullable=True),
        sa.Column("current_script_version_id", sa.String(26), nullable=True),
        sa.Column("generation_job_id", sa.String(26), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('created','source_processing','source_ready',"
            "'script_generating','script_ready','failed','archived')",
            name="ck_podcasts_state",
        ),
    )
    op.create_index("ix_podcasts_user_id", "podcasts", ["user_id"])

    op.create_table(
        "podcast_source_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "podcast_id",
            sa.String(26),
            sa.ForeignKey("podcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column(
            "media_id",
            sa.String(26),
            sa.ForeignKey("media_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("parse_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("podcast_id", "revision", name="uq_podcast_source_revision"),
        sa.CheckConstraint(
            "parse_status IN ('pending','parsing','parsed','failed')",
            name="ck_podcast_source_parse",
        ),
    )
    op.create_index(
        "ix_podcast_source_versions_podcast_id", "podcast_source_versions", ["podcast_id"]
    )

    op.create_table(
        "podcast_script_versions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "podcast_id",
            sa.String(26),
            sa.ForeignKey("podcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_version_id",
            sa.String(26),
            sa.ForeignKey("podcast_source_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column(
            "runtime_snapshot_id",
            sa.String(26),
            sa.ForeignKey("runtime_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("outline", sa.Text, nullable=False),
        sa.Column("target_duration_seconds", sa.Integer, nullable=False),
        sa.Column("estimated_duration_seconds", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("podcast_id", "revision", name="uq_podcast_script_revision"),
        sa.CheckConstraint("target_duration_seconds > 0", name="ck_podcast_script_duration"),
        sa.CheckConstraint(
            "status IN ('draft','ready','archived')", name="ck_podcast_script_status"
        ),
    )
    op.create_index(
        "ix_podcast_script_versions_podcast_id", "podcast_script_versions", ["podcast_id"]
    )

    op.create_table(
        "podcast_segments",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "script_version_id",
            sa.String(26),
            sa.ForeignKey("podcast_script_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column(
            "agent_version_id",
            sa.String(26),
            sa.ForeignKey("agent_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("citations", sa.Text, nullable=True),
        sa.Column("estimated_ms", sa.Integer, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("script_version_id", "position", name="uq_podcast_segments_position"),
    )
    op.create_index(
        "ix_podcast_segments_script_version_id", "podcast_segments", ["script_version_id"]
    )

    op.create_table(
        "podcast_playbacks",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "podcast_id",
            sa.String(26),
            sa.ForeignKey("podcasts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "script_version_id",
            sa.String(26),
            sa.ForeignKey("podcast_script_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("room_name", sa.String(128), nullable=False, unique=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="created"),
        sa.Column("segment_cursor", sa.Integer, nullable=False, server_default="0"),
        sa.Column("offset_ms", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("branch_ref", sa.String(80), nullable=True),
        sa.Column("epoch", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("elapsed_ms", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("fencing_token", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.String(40), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "state IN ('created','playing','interrupted','resuming','closing','ended','failed')",
            name="ck_podcast_playbacks_state",
        ),
    )
    op.create_index("ix_podcast_playbacks_podcast_id", "podcast_playbacks", ["podcast_id"])

    op.create_table(
        "podcast_audio_cache",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "segment_id",
            sa.String(26),
            sa.ForeignKey("podcast_segments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("voice_config_hash", sa.String(64), nullable=False),
        sa.Column(
            "media_id",
            sa.String(26),
            sa.ForeignKey("media_objects.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "segment_id", "voice_config_hash", name="uq_podcast_audio_cache_segment"
        ),
    )
    op.create_index(
        "ix_podcast_audio_cache_owner_user_id", "podcast_audio_cache", ["owner_user_id"]
    )

    # Script versions ready bersifat immutable
    op.execute(
        "CREATE TRIGGER podcast_script_versions_no_update BEFORE UPDATE ON podcast_script_versions "
        "FOR EACH ROW WHEN (OLD.status = 'ready') EXECUTE FUNCTION prevent_mutation();"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS podcast_script_versions_no_update ON podcast_script_versions;"
    )
    op.drop_table("podcast_audio_cache")
    op.drop_table("podcast_playbacks")
    op.drop_table("podcast_segments")
    op.drop_table("podcast_script_versions")
    op.drop_table("podcast_source_versions")
    op.drop_table("podcasts")
