"""Phase 6: voice/video call schema

Revision ID: 0006_phase6_calls
Revises: 0005_phase5_media
Create Date: 2025-09-24

Sesuai .blueprint/postgresql-schema.md baris call_sessions dan call_turns.
Media/audio pipeline realtime berjalan di LiveKit worker (Phase 6 runtime);
tabel ini menyimpan state otoritatif, lease/fencing, dan turn checkpoints.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_phase6_calls"
down_revision = "0005_phase5_media"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "call_sessions",
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("conversation_sessions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("room_name", sa.String(128), nullable=False, unique=True),
        sa.Column("room_sid", sa.String(128), nullable=True, unique=True),
        sa.Column("state", sa.String(20), nullable=False, server_default="created"),
        sa.Column("end_reason", sa.String(40), nullable=True),
        sa.Column("consent_version", sa.String(40), nullable=False),
        sa.Column("lease_owner", sa.String(64), nullable=True),
        sa.Column("fencing_token", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("mode IN ('voice','video')", name="ck_call_sessions_mode"),
        sa.CheckConstraint(
            "state IN ('created','admitted','active','ending','ended','failed')",
            name="ck_call_sessions_state",
        ),
    )

    op.create_table(
        "call_turns",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("call_sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer, nullable=False),
        sa.Column("speaker", sa.String(20), nullable=False),
        sa.Column(
            "message_id",
            sa.String(26),
            sa.ForeignKey("conversation_messages.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("state", sa.String(20), nullable=False, server_default="started"),
        sa.Column("epoch", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("session_id", "sequence", name="uq_call_turns_sequence"),
        sa.CheckConstraint("speaker IN ('user','agent')", name="ck_call_turns_speaker"),
        sa.CheckConstraint(
            "state IN ('started','completed','interrupted','failed')",
            name="ck_call_turns_state",
        ),
    )
    op.create_index("ix_call_turns_session_id", "call_turns", ["session_id"])


def downgrade() -> None:
    op.drop_table("call_turns")
    op.drop_table("call_sessions")
