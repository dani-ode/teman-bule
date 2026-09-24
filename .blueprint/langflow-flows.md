# Langflow Workflow Specification

Langflow adalah pusat proses AI non-realtime dan background. Setiap flow punya immutable version, input/output JSON Schema, prompt version, model capabilities, timeout, cost owner, tool allowlist, evaluation dataset dan registry lifecycle `draft|staged|active|deprecated|disabled`. Flow export bersih dari secret disimpan saat implementasi; komponen custom di `custom_langflow_components/`.

## Deployment URLs

- UI/API dan resolusi file upload: `LANGFLOW_BASE_URL=http://localhost:7860`; dapat diganti melalui environment untuk server lain.
- Target eksekusi aplikasi: `POST {LANGFLOW_BASE_URL}/api/v2/workflows`, setelah deployment lolos DEC-10. Workflow API masih Beta pada dokumentasi Langflow 1.12.x dan memerlukan `LANGFLOW_DEVELOPER_API_ENABLED=true` di server Langflow.
- Konfigurasi repository saat ini masih memakai `LANGFLOW_RUN_PATH=/api/v1/run` dengan `{flow_id}` pada URL. Migrasi v2 harus memperbarui adapter, config dan fixtures bersama karena `flow_id` pindah ke request body; mengganti path saja tidak cukup. Tidak ada fallback otomatis v2 → v1. Jika deployment belum mendukung v2, penggunaan v1 harus dicatat eksplisit pada DEC-10.
- MCP streamable lokal: `http://localhost:7860/api/v1/mcp/project/298095b5-c03d-4229-b96b-e4ad9389d394/streamable`; sesuaikan `LANGFLOW_MCP_STREAMABLE_URL` dan `LANGFLOW_PROJECT_ID` saat berpindah deployment. Konfigurasi agent ada di `.agents/mcp_config.json`.
- Jalankan konfigurasi agent dari root repository. Launcher `.agents/langflow_mcp.py` membaca `LANGFLOW_MCP_STREAMABLE_URL` dan `LANGFLOW_API_KEY` dari `.env` (environment proses memiliki prioritas), lalu meneruskan key sebagai header `x-api-key`. Secret tidak disimpan di JSON MCP. Dependency proxy dibatasi `mcp<2` karena API `request_ctx` belum kompatibel dengan MCP 2.
- Endpoint MCP project mengekspos flow yang sudah dipublikasikan sebagai tool eksekusi; pembuatan/edit canvas memerlukan API Langflow atau server MCP manajemen terpisah.
- `LANGFLOW_INTERNAL_RUNTIME_BASE_URL` adalah alamat runtime gateway backend Teman Bule, bukan URL server Langflow.

## Kontrak Eksekusi HTTP

FastAPI dan durable worker memanggil HTTP API secara langsung. API key Langflow hanya digunakan server-side. Endpoint Workflow API bukan webhook; endpoint webhook Langflow v1 adalah `/api/v1/webhook/{flow_id_or_name}` dan tidak menjadi jalur chat aplikasi.

| Kebutuhan | Mode target | Perilaku |
|---|---|---|
| Chat awal / respons lengkap | `sync` | Tunggu JSON hasil lengkap |
| Chat interaktif | `stream` | SSE bertahap; backend memetakan event ke kontrak aplikasi |
| Background berdurasi terbatas | `sync` dari durable worker | Tetap background bagi pengguna; worker menunggu hasil |
| Workflow panjang | `background` | Simpan `job_id` Langflow pada attempt SQL dan pantau hingga terminal |

Contoh payload vendor minimal untuk chat (bukan keseluruhan kontrak otorisasi TemanBule):

```json
{
  "flow_id": "<published-flow-id>",
  "input_value": "Help me practice English",
  "session_id": "<backend-authorized-conversation-id>",
  "mode": "sync"
}
```

Untuk flow chat yang sesuai, jawaban utama berada pada `output.text`; adapter tetap memvalidasi status, error dan structured output. `session_id` dipetakan backend setelah ownership check, bukan bukti otorisasi dan bukan pengganti history canonical PostgreSQL.

Input envelope di bawah adalah kontrak aplikasi, bukan field top-level vendor yang dapat diasumsikan diterima. Adapter memetakan envelope ke input komponen yang terverifikasi; transport `execution_ref` mengikuti `runtime-components.md`. Mapping dan fixtures harus selesai sebelum aktivasi flow.

Mode `background` Langflow tidak menggantikan outbox/SQL jobs aplikasi. Acceptance atau `job_id` bukan completion. Timeout setelah dispatch dapat berarti outcome unknown; lakukan lookup/reconciliation sebelum menjalankan ulang. Verifikasi restart recovery, polling, streaming, cancellation dan mapping error di DEC-10.

Referensi vendor: [Workflow API quickstart](https://docs.langflow.org/workflow-api-quickstart). OpenAPI dan versi deployment yang benar menjadi acuan implementasi.

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

Langflow MCP untuk discovery dan eksekusi published flow oleh agent internal dengan service scopes. Kontrol lifecycle/import/edit hanya tersedia jika server MCP manajemen memang menyediakan tool tersebut; project MCP tidak diasumsikan memilikinya. Chat dan event background rutin memakai HTTP adapter karena workflow sudah dipilih backend. CallCraft MCP untuk schema/discovery/tool routing. MCP tidak menggantikan queue atau authorization dan tidak boleh expose arbitrary mutation/admin tool ke tutor. Exact transport, schemas, streaming/cancellation dan secret persistence harus diverifikasi di Phase 0.

Prompt persona bersama untuk Langflow dan realtime dipublish sebagai artifact versi + checksum. Deploy mencatat versi yang dipakai tiap session. Golden datasets mencakup bilingual tutoring, both agents, empty retrieval, prompt injection paper/memory, truthful tool reporting, partial output, BYOK failure dan metering. Aktivasi staged dan rollback menunjuk versi immutable.
