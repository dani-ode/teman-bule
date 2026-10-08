"""Phase 12: chat canvas wiring (tweak component + registry chat_turn & chat_background)

Revision ID: 0012_phase12_chat_canvas_wiring
Revises: 0011_phase11_podcast_flows
Create Date: 2026-02-14

Dua perubahan yang saling melengkapi untuk fitur chat berbasis Langflow
Workflow API v2:

1. ``ai_flow_registry`` mendapat dua kolom nama komponen canvas:
   - ``input_tweak_component``  — nama komponen Webhook/Custom input di canvas
     yang menjadi target key ``tweaks`` saat backend memanggil
     ``POST /api/v2/workflows``. Contoh body contoh pengguna memakai nama acak
     per-canvas (``Webhook-Nbrfi``, ``Webhook-HxHuc``, ``Webhook-ua2By``) yang
     berbeda dari default ``json_input`` yang di-hardcode adapter lama.
     Menyimpan nama ini di registry membuat backend mengikuti canvas apa pun
     tanpa perlu mengubah kode setiap kali flow diekspor ulang.
   - ``output_component_name``  — nama komponen output terminal yang dibaca
     dari respons workflow (opsional; backend saat ini mem-parsing
     ``body.output`` secara langsung).
   Kedua kolom nullable dengan backfill ``json_input`` agar baris lama
   (chat_session_create, podcast_*) tetap bekerja dengan adapter yang ada.

2. Seed dua purpose chat yang selama ini hanya ada sebagai desain/adapter
   tanpa baris registry aktif untuk environment development:
   - ``chat_turn``       — flow id 46c932ae-d6de-4c1e-8e2f-97a7447cf259,
     mode sync, input component ``Webhook-HxHuc`` (message turn).
   - ``chat_background`` — flow id a7648fb6-f33d-4250-8621-9fd3abc6394b,
     mode background, input component ``Webhook-ua2By``. Flow ini dipicu oleh
     node ``background_dispatch`` DI DALAM canvas chat_turn (Langflow memicu
     dirinya sendiri), sehingga baris ini adalah referensi/allowlist untuk
     deployment, BUKAN dipanggil langsung oleh backend.
   Keduanya didaftarkan ``active`` untuk development agar
   ``resolve_flow(environment='development', purpose=...)`` berhasil.

Catatan: flow id greeting ``4b5acae1-...`` (purpose ``chat_session_create``)
sudah didaftarkan di migrasi 0010 dengan input component default
``json_input``; migrasi ini hanya mem-backfill nama komponennya, tidak
mengubah flow id-nya.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_phase12_chat_canvas_wiring"
down_revision = "0011_phase11_podcast_flows"
branch_labels = None
depends_on = None

# --- Registry rows (development) -------------------------------------------
CHAT_TURN_FLOW_ID = "46c932ae-d6de-4c1e-8e2f-97a7447cf259"
CHAT_TURN_ROW_ID = "01KCHATTURN00000000000001"
CHAT_TURN_INPUT_COMPONENT = "Webhook-HxHuc"

CHAT_BACKGROUND_FLOW_ID = "a7648fb6-f33d-4250-8621-9fd3abc6394b"
CHAT_BACKGROUND_ROW_ID = "01KCHATBACKGROUND00000001"
CHAT_BACKGROUND_INPUT_COMPONENT = "Webhook-ua2By"

SESSION_CREATE_INPUT_COMPONENT = "Webhook-Nbrfi"  # greeting (flow 4b5acae1-...)

# Default kompatibel dengan adapter lama yang men-hardcode tweaks json_input.
LEGACY_DEFAULT_INPUT_COMPONENT = "json_input"


def upgrade() -> None:
    # 1) Kolom nama komponen canvas pada ai_flow_registry.
    op.add_column(
        "ai_flow_registry",
        sa.Column("input_tweak_component", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "ai_flow_registry",
        sa.Column("output_component_name", sa.String(length=128), nullable=True),
    )

    # Backfill baris lama ke default adapter saat ini supaya tidak regresi.
    op.execute(
        sa.text(
            "UPDATE ai_flow_registry SET input_tweak_component = :default_comp"
            " WHERE input_tweak_component IS NULL"
        ).bindparams(default_comp=LEGACY_DEFAULT_INPUT_COMPONENT)
    )
    # Greeting flow (0010) memakai nama komponen canvas-nya sendiri.
    op.execute(
        sa.text(
            "UPDATE ai_flow_registry SET input_tweak_component = :comp"
            " WHERE purpose = 'chat_session_create'"
            " AND langflow_flow_id = '4b5acae1-8453-4419-970b-d23abdc8addd'"
        ).bindparams(comp=SESSION_CREATE_INPUT_COMPONENT)
    )

    # 2) Seed registry chat_turn + chat_background (development).
    # DB lokal mungkin sudah punya baris active untuk purpose ini dengan id
    # berbeda (hasil konfigurasi manual). Karena flow id-nya sama, kita
    # konsolidasi: UPDATE baris existing in-place bila ada, INSERT canonical
    # bila belum ada sama sekali. Ini menghindari pelanggaran KEDUA constraint:
    #   - uq_flow_registry_one_active (partial: 1 active per env+purpose)
    #   - uq_flow_registry_env_purpose_version (env+purpose+flow_version)
    for purpose, flow_id, flow_version, in_schema, out_schema, timeout_ms, input_comp, output_comp in (
        (
            "chat_turn",
            CHAT_TURN_FLOW_ID,
            "chat-turn.v1",
            "3",  # envelope chat_turn (lihat chat-workflow.v1.json)
            "1",  # ChatResult schema_version="1"
            60000,
            CHAT_TURN_INPUT_COMPONENT,
            "chat_response",
        ),
        (
            "chat_background",
            CHAT_BACKGROUND_FLOW_ID,
            "chat-bg.v1",
            "1",  # input_schema chat-background-workflow.v1.json
            "1",
            300000,
            CHAT_BACKGROUND_INPUT_COMPONENT,
            "background_result",
        ),
    ):
        row_id = CHAT_TURN_ROW_ID if purpose == "chat_turn" else CHAT_BACKGROUND_ROW_ID
        # a) Bila ada baris active dengan flow id yang sama (konfigurasi manual),
        #    selaraskan nama komponen + schema + flow_version-nya in-place.
        #    flow_version hanya diubah bila belum ada baris lain dengan
        #    (env, purpose, flow_version) target, agar tidak bentrok unique.
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
                input_component=input_comp,
                output_component=output_comp,
                in_schema=in_schema,
                out_schema=out_schema,
                timeout_ms=timeout_ms,
                flow_version=flow_version,
                purpose=purpose,
                flow_id=flow_id,
            )
        )
        # b) Bila TIDAK ada baris active untuk flow id ini sama sekali,
        #    nonaktifkan baris active lain untuk purpose ini lalu INSERT canonical.
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
            ).bindparams(purpose=purpose, flow_id=flow_id)
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
                id=row_id,
                purpose=purpose,
                flow_version=flow_version,
                flow_id=flow_id,
                in_schema=in_schema,
                out_schema=out_schema,
                timeout_ms=timeout_ms,
                input_component=input_comp,
                output_component=output_comp,
            )
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM ai_flow_registry WHERE id IN (:turn_id, :bg_id)"
        ).bindparams(turn_id=CHAT_TURN_ROW_ID, bg_id=CHAT_BACKGROUND_ROW_ID)
    )
    op.execute(
        sa.text(
            "UPDATE ai_flow_registry SET input_tweak_component = :default_comp,"
            " output_component_name = NULL"
            " WHERE purpose = 'chat_session_create'"
            " AND langflow_flow_id = '4b5acae1-8453-4419-970b-d23abdc8addd'"
        ).bindparams(default_comp=LEGACY_DEFAULT_INPUT_COMPONENT)
    )
    op.drop_column("ai_flow_registry", "output_component_name")
    op.drop_column("ai_flow_registry", "input_tweak_component")
