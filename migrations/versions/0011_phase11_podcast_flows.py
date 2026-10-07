"""Phase 11: registry flow podcast (document ingestion + script generation)

Revision ID: 0011_phase11_podcast_flows
Revises: 0010_phase10_chat_session_create_flow
Create Date: 2026-02-13

Mendaftarkan dua workflow Langflow podcast ke ai_flow_registry untuk
environment development:

- ``podcast_document_ingestion`` (flow id ebe688b6-b693-44e2-9fb0-47edc1b378bf)
  — dipicu outbox ``podcast.source_uploaded.v1``, dieksekusi worker dengan
  mode ``background`` (komponen input Webhook-iGh25); worker memantau job_id
  hingga terminal sebelum menandai source parsed.
- ``podcast_script_generation`` (flow id 41aec3d5-941d-4919-a2e8-cc3c4ad08c0f)
  — dipanggil mode ``sync`` saat pengguna menekan play (komponen input
  Webhook-qTk7h); menghasilkan outline + segments Elean/Willy + citations.

Nama komponen Webhook di atas adalah bagian kontrak deployment flow; adapter
memetakannya dari modul podcasts.langflow_adapter, bukan dari registry.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_phase11_podcast_flows"
down_revision = "0010_chat_session_create_flow"
branch_labels = None
depends_on = None

INGESTION_FLOW_ID = "ebe688b6-b693-44e2-9fb0-47edc1b378bf"
INGESTION_ROW_ID = "01KPODCASTINGEST0000000001"
SCRIPT_FLOW_ID = "41aec3d5-941d-4919-a2e8-cc3c4ad08c0f"
SCRIPT_ROW_ID = "01KPODCASTSCRIPTGEN0000001"


def upgrade() -> None:
    insert_stmt = sa.text(
        "INSERT INTO ai_flow_registry ("
        "  id, environment, purpose, flow_version, langflow_flow_id,"
        "  input_schema_version, output_schema_version, prompt_version,"
        "  required_capabilities, tool_allowlist, timeout_ms, status"
        ") VALUES ("
        "  :id, :environment, :purpose, :flow_version, :flow_id,"
        "  '1', '1', NULL, NULL, '[]', :timeout_ms, 'active'"
        ")"
    )
    op.execute(
        insert_stmt.bindparams(
            id=INGESTION_ROW_ID,
            environment="development",
            purpose="podcast_document_ingestion",
            flow_version="podcast-ing.v1",
            flow_id=INGESTION_FLOW_ID,
            timeout_ms=120000,
        )
    )
    op.execute(
        insert_stmt.bindparams(
            id=SCRIPT_ROW_ID,
            environment="development",
            purpose="podcast_script_generation",
            flow_version="podcast-script.v1",
            flow_id=SCRIPT_FLOW_ID,
            timeout_ms=120000,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM ai_flow_registry WHERE purpose IN"
            " ('podcast_document_ingestion', 'podcast_script_generation')"
            " AND langflow_flow_id IN (:ingestion_id, :script_id)"
        ).bindparams(ingestion_id=INGESTION_FLOW_ID, script_id=SCRIPT_FLOW_ID)
    )
