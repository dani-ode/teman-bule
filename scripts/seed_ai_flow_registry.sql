-- Seed ai_flow_registry (idempotent) untuk environment development.
-- Sumber kebenaran: migrasi 0010 (chat_session_create), 0011 (podcast flows),
-- 0012 (chat canvas wiring: kolom input_tweak_component + chat_turn/chat_background).
-- Gunakan file ini untuk me-restore baris registry yang terhapus tanpa
-- menjalankan ulang migrasi alembic.
--
-- Jalankan:
--   docker compose --env-file .env --env-file .env.deploy exec -T postgres \
--     psql -U temanbule -d temanbule < scripts/seed_ai_flow_registry.sql
--
-- Idempotensi: ON CONFLICT (environment, purpose, flow_version) DO UPDATE
-- menyelaraskan flow id + komponen canvas tanpa menduplikasi baris. Partial
-- unique index uq_flow_registry_one_active (1 active per env+purpose) aman
-- karena seed ini hanya menulis satu baris per purpose; baris active lain
-- untuk purpose yang sama (konfigurasi manual) dinonaktifkan dulu di bawah.

BEGIN;

-- Baris active lain untuk purpose yang sama (jika ada, sisa konfigurasi
-- manual) di-staged supaya seed ini bisa memasang baris canonical-nya.
UPDATE ai_flow_registry SET status = 'staged'
WHERE environment = 'development' AND status = 'active'
  AND purpose IN (
    'chat_session_create', 'chat_turn', 'chat_background',
    'conversation_ingestion',
    'podcast_document_ingestion', 'podcast_script_generation',
    'chat_suggestion'
  )
  AND (environment, purpose, flow_version) NOT IN (
    ('development', 'chat_session_create', 'v1'),
    ('development', 'chat_turn', 'chat-turn.v1'),
    ('development', 'chat_background', 'chat-bg.v1'),
    ('development', 'conversation_ingestion', 'conv-ing.v1'),
    ('development', 'podcast_document_ingestion', 'podcast-ing.v1'),
    ('development', 'podcast_script_generation', 'podcast-script.v1'),
    ('development', 'chat_suggestion', 'suggestion.v1')
  );

-- ===== Greeting message (migrasi 0010 + backfill komponen 0012) ============
-- flow 4b5acae1-... ; mode sync ; tweaks Webhook-Nbrfi
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KCHATSESSIONCREATE000001', 'development', 'chat_session_create', 'v1',
  '4b5acae1-8453-4419-970b-d23abdc8addd',
  '1', '1', NULL, NULL, '[]', 60000, 'active',
  'Webhook-Nbrfi', NULL
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Message turn (migrasi 0012) =========================================
-- flow 46c932ae-... ; mode sync ; tweaks Webhook-HxHuc
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KCHATTURN00000000000001', 'development', 'chat_turn', 'chat-turn.v1',
  '46c932ae-d6de-4c1e-8e2f-97a7447cf259',
  '3', '1', NULL, NULL, '[]', 60000, 'active',
  'Webhook-HxHuc', 'chat_response'
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Chat background (migrasi 0012; referensi/allowlist, dipicu dari
-- dalam canvas chat_turn oleh Langflow sendiri, bukan oleh backend) ========
-- flow a7648fb6-... ; mode background ; tweaks Webhook-ua2By
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KCHATBACKGROUND00000001', 'development', 'chat_background', 'chat-bg.v1',
  'a7648fb6-f33d-4250-8621-9fd3abc6394b',
  '1', '1', NULL, NULL, '[]', 300000, 'active',
  'Webhook-ua2By', 'background_result'
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Conversation ingestion (background; dipicu worker setelah session
-- chat/call/podcast selesai; flow menulis canonical langsung ke Postgres
-- via tool MCP conversation.persist_extraction — fire-and-forget) =========
-- flow 9cce08d2-... ; mode background ; tweaks Webhook-wcndY
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KCONVINGESTION000000001', 'development', 'conversation_ingestion',
  'conv-ing.v1', '9cce08d2-844d-40ec-b921-99436e8cf8c1',
  '1', '1', NULL, NULL,
  '["conversation.get_messages","conversation.persist_extraction"]', 300000, 'active',
  'Webhook-wcndY', NULL
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  tool_allowlist = EXCLUDED.tool_allowlist,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Podcast document ingestion (migrasi 0011 + komponen dari 0012) ======
-- flow ebe688b6-... ; mode background ; tweaks Webhook-iGh25
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KPODCASTINGEST0000000001', 'development', 'podcast_document_ingestion',
  'podcast-ing.v1', 'ebe688b6-b693-44e2-9fb0-47edc1b378bf',
  '1', '1', NULL, NULL, '[]', 120000, 'active',
  'Webhook-iGh25', NULL
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Podcast script generation (migrasi 0011 + komponen dari 0012) =======
-- flow 41aec3d5-... ; mode sync ; tweaks Webhook-qTk7h
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KPODCASTSCRIPTGEN0000001', 'development', 'podcast_script_generation',
  'podcast-script.v1', '41aec3d5-941d-4919-a2e8-cc3c4ad08c0f',
  '1', '1', NULL, NULL, '[]', 120000, 'active',
  'Webhook-qTk7h', NULL
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Suggestion message (migrasi 0015) ===================================
-- flow db13d00d-... ; mode sync ; tweaks Webhook-Nbrfi
-- Dipicu dari conversation room: 3 contoh pesan untuk user yang baru belajar.
INSERT INTO ai_flow_registry (
  id, environment, purpose, flow_version, langflow_flow_id,
  input_schema_version, output_schema_version, prompt_version,
  required_capabilities, tool_allowlist, timeout_ms, status,
  input_tweak_component, output_component_name
) VALUES (
  '01KSUGGESTION00000000001', 'development', 'chat_suggestion',
  'suggestion.v1', 'db13d00d-fe41-4c1f-baca-9c0c2f6b3aa1',
  '1', '1', NULL, NULL, '[]', 60000, 'active',
  'Webhook-Nbrfi', 'suggestion_result'
)
ON CONFLICT (environment, purpose, flow_version) DO UPDATE SET
  langflow_flow_id = EXCLUDED.langflow_flow_id,
  input_schema_version = EXCLUDED.input_schema_version,
  output_schema_version = EXCLUDED.output_schema_version,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active',
  input_tweak_component = EXCLUDED.input_tweak_component,
  output_component_name = EXCLUDED.output_component_name;

-- ===== Tool registry: conversation.get_messages (pull pesan oleh flow
-- ingestion; read-only → idempotency tidak diwajibkan) ======================
INSERT INTO tool_registry (
  id, environment, name, schema_version, callcraft_spec_id, scope,
  idempotency_policy, timeout_ms, status
) VALUES (
  '01KTOOLCONVGETMSGS00000001', 'development', 'conversation.get_messages',
  '1', 'tools.v1.json#conversation.get_messages', 'ingestion:read',
  'not_required', 30000, 'active'
)
ON CONFLICT (environment, name, schema_version) DO UPDATE SET
  scope = EXCLUDED.scope,
  idempotency_policy = EXCLUDED.idempotency_policy,
  timeout_ms = EXCLUDED.timeout_ms,
  status = 'active';

COMMIT;

-- Verifikasi cepat:
SELECT purpose, flow_version, langflow_flow_id, input_tweak_component, status
FROM ai_flow_registry
WHERE environment = 'development'
ORDER BY purpose;
