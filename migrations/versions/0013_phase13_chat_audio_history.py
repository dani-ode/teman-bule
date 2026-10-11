"""Phase 13: chat audio history (conversation_messages.audio_duration_ms)

Revision ID: 0013_phase13_chat_audio_history
Revises: 0012_phase12_chat_canvas_wiring
Create Date: 2026-02-20

Fitur chat audio: history room harus bisa me-render ulang audio bubble
(pesan agent hasil TTS dan voice message dari user) setelah user keluar/masuk
room. Dua perubahan skema:

1. ``conversation_messages.audio_duration_ms`` — durasi audio dalam milidetik.
   Live response Langflow sudah membawa nilai ini, tetapi sebelumnya tidak
   dipersist sehingga hilang saat history dimuat ulang. Nullable karena
   pesan text dan data lama tidak memilikinya. Append-only tetap terjaga:
   kolom hanya diisi saat INSERT pesan baru.
2. Index pada ``conversation_messages.media_id`` — history endpoint melakukan
   batch lookup media_objects untuk me-resolve presigned download URL dari
   storage_key; index menjaga lookup tetap murah.

Catatan: ``media_id`` sengaja tetap tanpa FK constraint — audio TTS agent
ditulis langsung ke bucket oleh workflow Langflow dan tidak selalu memiliki
baris media_objects (best-effort link via storage_key); FK akan menolak
pesan agent yang sah.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_phase13_chat_audio_history"
down_revision = "0012_phase12_chat_canvas_wiring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column("audio_duration_ms", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_conversation_messages_media_id",
        "conversation_messages",
        ["media_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_media_id", table_name="conversation_messages"
    )
    op.drop_column("conversation_messages", "audio_duration_ms")
