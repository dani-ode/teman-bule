"""Phase 16: tabel auth_email_deliveries (dedupe pengiriman email auth)

Revision ID: 0016_auth_email_deliveries
Revises: 0015_phase15_suggestion_flow
Create Date: 2026-10-22

Tabel dedupe untuk handler event ``auth.email_requested.v1``. Baris di-insert
SEBELUM email dikirim; delivery konkuren dari event yang sama yang kalah race
melempar IntegrityError (duplicate key) sehingga delivery kedua di-skip.

Tabel ini sebelumnya hanya ada di environment development lokal dan belum
pernah dimigrasikan, sehingga handler email gagal DBAPIError
(UndefinedTable) di environment yang fresh (server produksi).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016_auth_email_deliveries"
down_revision = "0015_phase15_suggestion_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: tabel ini pernah dibuat manual di environment development
    # lokal, sehingga create_table polos akan gagal di sana. checkfirst=True
    # me-skip pembuatan bila tabel sudah ada; environment fresh (produksi)
    # tetap mendapatkan tabelnya.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "auth_email_deliveries" in inspector.get_table_names():
        return
    op.create_table(
        "auth_email_deliveries",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("token_hash", name="auth_email_deliveries_pkey"),
    )


def downgrade() -> None:
    op.drop_table("auth_email_deliveries")
