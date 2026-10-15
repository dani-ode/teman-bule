"""Phase 14: registry flow STT (purpose 'stt') untuk transkripsi voice message

Revision ID: 0014_phase14_stt_flow
Revises: 0013_phase13_chat_audio_history
Create Date: 2026-02-21

Alur voice message berubah menjadi 3-hit dari client:
upload media → POST /v1/media/{media_id}:transcribe → kirim chat turn
(media_id + text hasil transkripsi). Dengan alur ini flow ``chat_turn``
tidak lagi melakukan STT; teks pesan user sudah final sejak INSERT ke
``conversation_messages`` — tabel tetap append-only murni, tanpa UPDATE.

Migrasi ini mendaftarkan flow STT (Langflow Workflow API v2, mode sync)
ke ``ai_flow_registry`` untuk environment development, mengikuti pola
konsolidasi migration 0012 (UPDATE in-place bila baris active untuk flow
id yang sama sudah ada; nonaktifkan baris active lain; INSERT canonical
bila belum ada).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_phase14_stt_flow"
down_revision = "0013_phase13_chat_audio_history"
branch_labels = None
depends_on = None

# --- Registry row (development) --------------------------------------------
STT_FLOW_ID = "140481c9-41cd-4675-8563-0d034f76b9cb"
STT_ROW_ID = "01KSTT000000000000000001"
STT_INPUT_COMPONENT = "Webhook-dMtjQ"


def upgrade() -> None:
    # a) Bila ada baris active dengan flow id yang sama (konfigurasi manual),
    #    selaraskan nama komponen + schema + flow_version-nya in-place.
    op.execute(
        sa.text(
            "UPDATE ai_flow_registry SET"
            "  input_tweak_component = :input_component,"
            "  output_component_name = :output_component,"
            "  input_schema_version = :in_schema,"
            "  output_schema_version = :out_schema,"
            "  timeout_ms = :timeout_ms,"
            "  flow_version = CASE"
            "    WHEN NOT EXISTS ("
            "      SELECT 1 FROM ai_flow_registry existing"
            "      WHERE existing.environment = 'development'"
            "        AND existing.purpose = :purpose"
            "        AND existing.flow_version = :flow_version"
            "        AND existing.langflow_flow_id <> :flow_id"
            "    ) THEN :flow_version ELSE flow_version END"
            " WHERE environment = 'development' AND purpose = :purpose"
            " AND langflow_flow_id = :flow_id AND status = 'active'"
        ).bindparams(
            input_component=STT_INPUT_COMPONENT,
            output_component="stt_result",
            in_schema="1",
            out_schema="1",
            timeout_ms=30000,
            flow_version="stt.v1",
            purpose="stt",
            flow_id=STT_FLOW_ID,
        )
    )
    # b) Bila TIDAK ada baris active untuk flow id ini, nonaktifkan baris
    #    active lain untuk purpose ini lalu INSERT canonical.
    op.execute(
        sa.text(
            "UPDATE ai_flow_registry SET status = 'staged'"
            " WHERE environment = 'development' AND purpose = :purpose"
            " AND status = 'active' AND langflow_flow_id <> :flow_id"
            " AND NOT EXISTS ("
            "   SELECT 1 FROM ai_flow_registry active_same"
            "   WHERE active_same.environment = 'development'"
            "     AND active_same.purpose = :purpose"
            "     AND active_same.langflow_flow_id = :flow_id"
            "     AND active_same.status = 'active'"
            " )"
        ).bindparams(purpose="stt", flow_id=STT_FLOW_ID)
    )
    op.execute(
        sa.text(
            "INSERT INTO ai_flow_registry ("
            "  id, environment, purpose, flow_version, langflow_flow_id,"
            "  input_schema_version, output_schema_version, prompt_version,"
            "  required_capabilities, tool_allowlist, timeout_ms, status,"
            "  input_tweak_component, output_component_name"
            ")"
            " SELECT :id, 'development', :purpose, :flow_version, :flow_id,"
            "  :in_schema, :out_schema, NULL, NULL, '[]', :timeout_ms, 'active',"
            "  :input_component, :output_component"
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM ai_flow_registry t"
            "   WHERE t.environment = 'development' AND t.purpose = :purpose"
            "     AND t.langflow_flow_id = :flow_id AND t.status = 'active'"
            " )"
            " ON CONFLICT (id) DO NOTHING"
        ).bindparams(
            id=STT_ROW_ID,
            purpose="stt",
            flow_version="stt.v1",
            flow_id=STT_FLOW_ID,
            in_schema="1",
            out_schema="1",
            timeout_ms=30000,
            input_component=STT_INPUT_COMPONENT,
            output_component="stt_result",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM ai_flow_registry WHERE id = :row_id").bindparams(
            row_id=STT_ROW_ID
        )
    )
