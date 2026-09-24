# Langflow Workflow Specification

Langflow adalah pusat proses AI non-realtime dan background. Setiap flow punya immutable version, input/output JSON Schema, prompt version, model capabilities, timeout, cost owner, tool allowlist, evaluation dataset dan registry lifecycle `draft|staged|active|deprecated|disabled`. Flow export bersih dari secret disimpan saat implementasi; komponen custom di `custom_langflow_components/`.

## Deployment URLs

- UI/API dan resolusi file upload: `LANGFLOW_BASE_URL=https://langflow.flyup.id`.
- Eksekusi flow: `https://langflow.flyup.id/api/v1/run/{flow_id}` (`LANGFLOW_RUN_PATH=/api/v1/run`).
- MCP streamable: `https://langflow.flyup.id/api/v1/mcp/project/{project_id}/streamable`; isi `LANGFLOW_MCP_STREAMABLE_URL` sesuai `LANGFLOW_PROJECT_ID` deployment. Konfigurasi agent ada di `.agents/mcp_config.json`.
- `LANGFLOW_INTERNAL_RUNTIME_BASE_URL` adalah alamat runtime gateway backend Teman Bule, bukan URL server Langflow.

## Workflow Wajib

Draft machine-readable daftar flow dan allowlist: `custom_langflow_components/flows.v1.json`. Kontrak adapter: `custom_langflow_components/callcraft-component.v1.json`. Keduanya bukan canvas export; lihat `contract-artifacts.md` untuk schema/export gates dan hubungan registry.

| Flow key | Trigger dan hasil | Cost/credential | Tools |
|---|---|---|---|
| `practice_interaction` | Teks/transcript → RAG agent + private memory → tutor response/correction | Plan LLM | vocabulary + preference subset |
| `learning_assistance` | Pertanyaan + published version → grounded answer/citation | Plan LLM | progress read |
| `voice_note_transcription` | Scanned media → STT transcript | Plan STT | None |
| `conversation_ingestion` | Persisted range chat/call/podcast → canonical summary/evidence | Platform background | persist extraction |
| `user_fact_extraction` | Evidence range → proposed/confirmed facts with provenance | Platform background | facts upsert |
| `learning_assessment` | Interaction evidence → level/dimension feedback | Platform background | assessment record |
| `learning_content_ingestion` | Published version → canonical chunks | Platform background if needed | None |
| `agent_knowledge_ingestion` | Published Elean/Willy knowledge → canonical chunks | Platform background if needed | None |
| `podcast_document_ingestion` | Authorized scanned PDF → text/pages/chunks | Platform deterministic extraction | None |
| `podcast_script_generation` | Chunks + two persona versions → outline/segments/citations | Plan LLM | None |
| `toefl_evaluation` | Locked answers + rubric → bounded subjective score/feedback | Plan LLM; objective deterministic | record evaluation |
| `toefl_feedback_ingestion` | Committed score → canonical feedback chunks | Platform background | None |
| `dual_embedding_dispatch` | Canonical source commit → jobs for both active profiles | Platform | None |
| `embedding_projection_gemini` | Chunk refs → Gemini embed → own Astra collection | Admin Gemini | None |
| `embedding_projection_openai` | Chunk refs → OpenAI embed → own Astra collection | Admin OpenAI | None |
| `session_context_preparation` | Before call/playback → bounded authorized context snapshot | Platform background | Read-only allowed |

Tidak ada `realtime_turn` Langflow: direct realtime worker menangani voice/video/podcast interupsi. Auth, wallet, payment, room lifecycle, CRUD dan job retries bukan reasoning flow. Canvas boleh mengorkestrasi branch, tetapi durable completion/retry tetap SQL job + worker; menjalankan dua node tidak membuktikan dual projection selesai.

## Input Envelope v2

Required: `schema_version`, `request_id`, `traceparent`, `execution_context_token`, `purpose`, `resource_refs`, `runtime_snapshot_id`, `source_version`, `input`, `policy`. `input` hanya data pengguna/reference; `policy` dibangun backend: tools, retrieval scopes, budget, output schema. `ai_configuration` memuat model ID, ephemeral credential reference dan payer, bukan key. Actor/agent IDs dan owner berasal verified context; queue menyimpan IDs, bukan bearer token atau plaintext input panjang.

Background job mendapatkan execution context dan credential reference baru pada setiap attempt setelah ownership/status/snapshot diperiksa. Source canonical diambil lewat internal narrow endpoints. AI-selected mutasi wajib CallCraft; trusted component checkpointing/canonical data IO bukan function call dan memakai runtime API dengan scope terpisah.

## Output dan Metering

Envelope: schema/version, request ID, flow version, status, validated result, source refs/citations, tool execution refs, invocation usage refs dan checkpoint. Result interaction: text, corrections, suggested reply, citations, verified tool outcomes. Structured failures bukan jawaban sukses palsu.

Usage berasal adapter provider tepercaya, bukan angka yang ditulis LLM. Semua sub-invocation mempunyai ID, payer/capability, provider request ID, meter/version dan reservation reference jika VIP. Stream events mempunyai increasing sequence dan tepat satu terminal. Cancel harus merambat ke provider; biaya unknown direkonsiliasi.

## Alur Background

Persist interaction range → ingestion summary/evidence → facts extraction dan assessment jobs terpisah → canonical chunks → fan-out embeddings. Source ranges + flow version menjadi dedupe key. Fakta low-confidence berstatus proposed; learner preferences eksplisit mengungguli inferensi. Assessment menyimpan rubric/evidence, tidak langsung mengganti level profil tanpa policy/konfirmasi.

PDF parsing terjadi dalam sandbox bounded CPU/memory; Langflow mengorkestrasi komponen extractor, tidak mengeksekusi file sebagai kode. Script generation memakai canonical chunks dan page refs, memvalidasi speaker hanya Elean/Willy, durasi dan grounded citations sebelum ready. Objective TOEFL scorer deterministik tidak dapat dioverride evaluator.

## MCP dan Governance

Langflow MCP untuk discovery/control lifecycle, import/run sesuai deployed capability dengan service scopes. CallCraft MCP untuk schema/discovery/tool routing. MCP tidak berarti queue atau authorization dan tidak boleh expose arbitrary mutation/admin tool ke tutor. Exact transport, schemas, streaming/cancellation dan secret persistence harus diverifikasi di Phase 0.

Prompt persona bersama untuk Langflow dan realtime dipublish sebagai artifact versi + checksum. Deploy mencatat versi yang dipakai tiap session. Golden datasets mencakup bilingual tutoring, both agents, empty retrieval, prompt injection paper/memory, truthful tool reporting, partial output, BYOK failure dan metering. Aktivasi staged dan rollback menunjuk versi immutable.
