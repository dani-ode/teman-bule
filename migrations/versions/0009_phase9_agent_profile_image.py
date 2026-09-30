"""Phase 9: agent profile image

Revision ID: 0009_phase9_agent_profile_image
Revises: 0008_phase8_category_image
Create Date: 2026-02-02

Menambahkan kolom profile_image_key pada tabel agents: path object foto profil
agent di bucket S3/MinIO (mis. "model_profile/elean.jpeg"). Nilai URL
publik/signed dibangun di lapisan API dari profile_image_key, sehingga rotasi
bucket/endpoint tidak memerlukan migrasi data (pola yang sama dengan
practice_categories.image_key pada 0008).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_phase9_agent_profile_image"
down_revision = "0008_phase8_category_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("profile_image_key", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agents", "profile_image_key")
