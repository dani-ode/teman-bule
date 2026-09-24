# Implementation Plan dan Release Gates

**Tahap sekarang hanya spesifikasi/environment.** Fase berikut belum dikerjakan. Tiap fase coding wajib schema/migration dahulu, lint, strict types, meaningful unit/integration/contract tests, failure-path tests, artifact versioning dan operational docs sebelum dinyatakan selesai.

Pelaksanaan mengikuti milestone dan Definition of Ready/Done di `execution-readiness.md`. Keputusan terbuka dan spike dilacak di `decision-register.md`; status approved/verified hanya diberikan dengan bukti. Gate diterapkan pada ticket/capability terkait, sehingga keputusan fase lanjutan tidak menghambat foundation yang sudah Ready.

## Phase 0 — Keputusan dan Contract Spikes

Baseline draft JSON sudah tersedia dan diindeks di `contract-artifacts.md`. Gunakan sebagai input spike; keberadaan artefak tidak menutup gate vendor, rubric, batas payload atau runtime. `activation_gates` pada katalog wajib diselesaikan sebelum staged/active.

| Gate | Output wajib sebelum capability aktif |
|---|---|
| Komersial | Harga paket, currency/minor unit, token scale/rate card, refund/dispute policy, konfirmasi baseline biaya Advance |
| Xendit | Pilih produk/API version checkout/payment; auth webhook, status enum, event/reference dedupe, lookup, refund, sandbox/live verified fixtures |
| Models | Verify existing VIP model identifiers (nilai `.env` bukan bukti model tersedia), STT batch/stream, vision, usage/cancel semantics, Gemini/OpenAI model catalog |
| Embedding | Dua model/revision/dimension/task type/normalization, sepuluh collections, dual projection/reindex/filter tests |
| Langflow | Versi deployment, streaming/cancel, custom components, MCP, secret non-persistence, export/import, internal context/credential broker |
| CallCraft | Real JSON spec format/auth/MCP/context propagation/idempotency/errors; jangan menebak payload vendor |
| Google/email | Registered callback sama dengan env, domain/SMTP TLS, delivery, OAuth link/replay flows |
| Realtime/TTS | ElevenLabs model + kedua voice available, LiveKit SDK/plugins, concurrency, video frame policy, latency and cancellation |
| Produk/operasi | Persona details, rubric TOEFL, target/max podcast duration, extension, call quotas, retention/consent, SLO/RPO/RTO, domain/backup budget |

Exit: ADR/fixtures teredaksi dan approved policies. Tidak menjalankan billable provider probe tanpa controlled test budget. Tidak membuat model/price fallback agar startup tampak berhasil.

## Phase 1 — Foundation dan Auth

Pecahan pekerjaan: FND-01 sampai FND-10 di `foundation-backlog.md`; layout dan aturan dependensi mengikuti `backend-layout.md`. Tutup keputusan yang dibutuhkan masing-masing ticket sebelum coding, termasuk packaging, auth/config policy, transaction/replay dan execution grant semantics.

Python modular layout, dependency resolution/hash lock, config validation, structured errors/traces, Docker API/worker/realtime-worker, SQL/Redis, migrations, outbox/idempotency/audit. Implement email/password/verification/reset/refresh rotation, Google login + explicit linking. Exit: replay/CSRF/ownership tests, restart-safe email/event jobs, backup restore foundation.

## Phase 2 — Catalog, Agents, Plans dan Billing

Provider/model capabilities, encrypted LLM/STT BYOK and SSRF policy; Elean/Willy versions, plan selection/runtime snapshot. Ledger/reservations/usage adapter, packages/rates, Xendit checkout/inbox/reconciliation/refund. Exit: race saldo, double webhook, no-charge TTS/embedding, unknown usage recovery, secret redaction. Billing must precede billable vertical slice.

## Phase 3 — AI Control Plane dan Text Slice

Langflow/CallCraft registries, execution contexts, credential broker, practice stream, vocabulary tools, user-owned history. Langflow ingestion/facts/assessment; canonical documents + dual embedding; agent knowledge. Exit: both projections, single-branch failure retry, IDOR/citation tests, no fake tool success, repeat request without double debit.

Urutan vertical slice dalam fase ini:

1. `practice_interaction` lewat HTTP `sync`: input/output tersimpan, auth/ownership, persona, tool dan usage terbukti end-to-end.
2. Outbox → `conversation_ingestion`: proses rentang pesan yang sudah tersimpan; hasil canonical idempoten.
3. `dual_embedding_dispatch` → projection Gemini/OpenAI: job terpisah, satu branch gagal dapat diulang tanpa mengulang yang sukses. Fan-out memakai runtime API deterministik, bukan keputusan LLM.
4. Retrieval canonical memory kembali ke `practice_interaction`: filter owner, provenance dan source version tervalidasi.
5. `user_fact_extraction`, `learning_assessment` dan agent knowledge ingestion; masing-masing punya checkpoint dan retry sendiri.
6. Aktifkan chat `stream` dengan metering/partial/cancel semantics yang terbukti. Siapkan kontrak `session_context_preparation`; aktivasi untuk call/playback pada Phase 6–7.

Checkpoint pertama adalah chat → memory → retrieval yang berfungsi, bukan sekadar seluruh canvas tersedia. Semua exit criteria fase tetap berlaku sebelum fase dinyatakan selesai.

## Phase 4 — Home dan Profile/TOEFL

Published lesson versions/progress/Learn assistance; assessment dashboard and fact correction/deletion. TOEFL deterministic/objective + subjective flow, verified score tool and feedback dual indexing. Exit: scores immutable/bounded, rubric/provenance, plan-consistent async usage.

## Phase 5 — Private Media dan Voice Note

S3 upload finalize/scan/retention, voice note STT with selected plan, retry-safe transcript→chat. PDF sandbox/limits and canonical chunk extraction foundation. Exit: malicious/oversized/invalid media failure explicit; BYOK STT and VIP meter contract verified.

## Phase 6 — Voice dan Video Call

LiveKit room admission, direct STT/LLM/ElevenLabs worker, barge-in/epoch/fencing, video sampling/vision, usage rolling reserve, reconnect/durable checkpoints. Background Langflow after turns. Exit: no Langflow per-turn dependency, low-balance graceful end, no stale playback, disconnect/worker crash recovery and load/soak proof.

## Phase 7 — Podcast

Paper ingestion/chunk + projections, script generation, rename/versioning, Elean/Willy segments/voices, LiveKit single-director playback, interruption branches, resume/closing deadlines, cache isolation and billing. Exit: paper grounding, no duplicate generation debit, two distinct voices, repeated interruption cannot extend forever, restart recovery.

## Phase 8 — Production Readiness

E2E VIP/Advance across five menus, security/SSRF/prompt/tool evaluation, payment incident drills, KMS rotation, deletion across both vector spaces, database point-in-time restore, capacity tests, SLO dashboards, Apache/TLS, rollout/rollback and worker drain. Exit: measured limits and approved policies filled, no missing feature-required configuration, deployment contracts proven. Specification completeness is not production certification.
