"""Phase 5: media objects schema (upload/scan/retention)

Revision ID: 0005_phase5_media
Revises: 0004_phase4_learning_toefl
Create Date: 2025-09-24

Sesuai .blueprint/postgresql-schema.md baris media_objects. Voice note
transcript memakai conversation_messages (modality audio) yang sudah ada.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_phase5_media"
down_revision = "0004_phase4_learning_toefl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_objects",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.String(26),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(255), nullable=False, unique=True),
        sa.Column("media_type", sa.String(40), nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_upload"),
        sa.Column("scan_state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("bytes > 0", name="ck_media_objects_bytes"),
        sa.CheckConstraint(
            "media_type IN ('audio','image','pdf')",
            name="ck_media_objects_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_upload','uploaded','finalized','rejected','deleted')",
            name="ck_media_objects_status",
        ),
        sa.CheckConstraint(
            "scan_state IN ('pending','clean','malicious','error')",
            name="ck_media_objects_scan",
        ),
    )
    op.create_index("ix_media_objects_owner_user_id", "media_objects", ["owner_user_id"])
    op.create_index("ix_media_objects_status", "media_objects", ["status"])


def downgrade() -> None:
    op.drop_table("media_objects")
