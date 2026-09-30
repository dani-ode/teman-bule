"""Phase 8: practice category image + description

Revision ID: 0008_phase8_category_image
Revises: 0007_phase7_podcasts
Create Date: 2026-02-01

Menambahkan kolom image_key (path object di bucket S3/MinIO, mis.
"categories/daily_conversation.jpeg") dan description singkat agar daftar
kategori practice lebih menarik di UI. Nilai URL publik/signed dibangun di
lapisan API dari image_key, sehingga rotasi bucket/endpoint tidak memerlukan
migrasi data.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_phase8_category_image"
down_revision = "0007_phase7_podcasts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "practice_categories",
        sa.Column("image_key", sa.String(255), nullable=True),
    )
    op.add_column(
        "practice_categories",
        sa.Column("description", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("practice_categories", "description")
    op.drop_column("practice_categories", "image_key")
