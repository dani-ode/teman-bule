"""Phase 15: registry flow suggestion_message (purpose 'chat_suggestion')

Revision ID: 0015_phase15_suggestion_flow
Revises: 0014_phase14_stt_flow
Create Date: 2026-02-22

Fitur suggestion message: di conversation room, user yang baru belajar dapat
meminta 3 contoh pesan (suggestion) sebagai patokan memulai/melanjutkan
percakapan. Frontend menembak backend, backend meneruskan ke flow Langflow
``suggestion_message`` (mode sync) dengan session_id + category_id, lalu
mengembalikan 3 teks suggestion ke frontend.

Migrasi ini mendaftarkan flow suggestion ke ``ai_flow_registry`` untuk
environment development, mengikuti pola konsolidasi migration 0012/0014
(UPDATE in-place bila baris active untuk flow id yang sama sudah ada;
nonaktifkan baris active lain; INSERT canonical bila belum ada).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_phase15_suggestion_flow"
down_revision = "0014_phase14_stt_flow"
branch_labels = None
depends_on = None

# --- Registry row (development) --------------------------------------------
SUGGESTION_FLOW_ID = "db13d00d-fe41-4c1f-baca-9c0c2f6b3aa1"
SUGGESTION_ROW_ID = "01KSUGGESTION00000000001"
SUGGESTION_INPUT_COMPONENT = "Webhook-Nbrfi"


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
            input_component=SUGGESTION_INPUT_COMPONENT,
            output_component="suggestion_result",
            in_schema="1",
            out_schema="1",
            timeout_ms=60000,
            flow_version="suggestion.v1",
            purpose="chat_suggestion",
            flow_id=SUGGESTION_FLOW_ID,
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
        ).bindparams(purpose="chat_suggestion", flow_id=SUGGESTION_FLOW_ID)
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
            id=SUGGESTION_ROW_ID,
            purpose="chat_suggestion",
            flow_version="suggestion.v1",
            flow_id=SUGGESTION_FLOW_ID,
            in_schema="1",
            out_schema="1",
            timeout_ms=60000,
            input_component=SUGGESTION_INPUT_COMPONENT,
            output_component="suggestion_result",
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM ai_flow_registry WHERE id = :row_id").bindparams(
            row_id=SUGGESTION_ROW_ID
        )
    )
