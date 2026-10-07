"""Phase 10: registry flow chat_session_create

Revision ID: 0010_phase10_chat_session_create_flow
Revises: 0009_phase9_agent_profile_image
Create Date: 2026-02-12

Mendaftarkan workflow Langflow "Create New Chat Session"
(flow id 4b5acae1-8453-4419-970b-d23abdc8addd) ke ai_flow_registry dengan
purpose 'chat_session_create' untuk environment development. Flow ini dipakai
oleh POST /v1/practice/sessions untuk membuat session sekaligus chat pertama
dari AI dalam satu pemanggilan workflow.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_chat_session_create_flow"
down_revision = "0009_phase9_agent_profile_image"
branch_labels = None
depends_on = None

FLOW_ID = "4b5acae1-8453-4419-970b-d23abdc8addd"
REGISTRY_ROW_ID = "01KCHATSESSIONCREATE000001"


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO ai_flow_registry ("
            "  id, environment, purpose, flow_version, langflow_flow_id,"
            "  input_schema_version, output_schema_version, prompt_version,"
            "  required_capabilities, tool_allowlist, timeout_ms, status"
            ") VALUES ("
            "  :id, :environment, :purpose, :flow_version, :flow_id,"
            "  '1', '1', NULL, NULL, '[]', :timeout_ms, 'active'"
            ")"
        ).bindparams(
            id=REGISTRY_ROW_ID,
            environment="development",
            purpose="chat_session_create",
            flow_version="v1",
            flow_id=FLOW_ID,
            timeout_ms=60000,
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM ai_flow_registry WHERE purpose = 'chat_session_create'"
            " AND langflow_flow_id = :flow_id"
        ).bindparams(flow_id=FLOW_ID)
    )
