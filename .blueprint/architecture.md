# Architecture

## Gaya Arsitektur

**Modular monolith dengan DDD pragmatis dan ports/adapters.** Satu codebase backend mempunyai proses API, durable worker, dan realtime worker yang dapat dijalankan terpisah. Pemisahan proses mengikuti kebutuhan eksekusi; ownership domain tetap berada pada modul backend. Langflow adalah orchestrator AI eksternal, bukan pemilik aturan bisnis aplikasi.

- **DDD:** modul mengikuti kemampuan bisnis, istilah domain dan pemilik data. Invariants seperti saldo, ownership, published version dan lifecycle ditegakkan oleh modul pemilik.
- **Ports/adapters:** use case memakai interface; PostgreSQL, Langflow, CallCraft dan SDK vendor dihubungkan melalui adapter. HTTP, job consumer dan LiveKit menjadi pintu masuk ke use case yang sama.
- **Event-driven untuk background:** perubahan domain dan outbox disimpan atomik, lalu worker memproses event secara at-least-once. Ini bukan event sourcing; state otoritatif tetap tabel PostgreSQL.
- **Pragmatis:** aggregate/value object dipakai ketika ada invariant yang perlu dijaga. CRUD sederhana cukup dengan use case dan repository; tidak perlu microservices, CQRS penuh atau lapisan tambahan tanpa kebutuhan.

Ini adalah arsitektur target. Struktur source dan kepatuhan boundary dibuktikan saat implementasi; folder saja tidak membuktikan penerapan DDD. Detail modul ada di `backend-layout.md`, transport workflow di `langflow-flows.md`.

## Boundary dan Otoritas

| Komponen | Tanggung jawab |
|---|---|
| Backend modules melalui FastAPI/use cases | Auth, ownership, katalog/plan resolver, payment order Xendit, wallet ledger, reservasi, credential broker, CRUD/state, room admission, signed media, outbox |
| Realtime worker | LiveKit media, VAD/STT, streaming LLM langsung, frame vision, ElevenLabs, turn cancellation, podcast director, usage checkpoints |
| Langflow | Chat/Learn reasoning, RAG, PDF/chunk/script, ekstraksi fakta, assessment, TOEFL subjektif, dua embedding projection, background orchestration AI |
| CallCraft | Registry/schema, routing dan eksekusi seluruh tool/function call dari Langflow maupun realtime worker |
| PostgreSQL | Otoritas account, pesan, fakta, score, canonical chunks, ledger, payment, registry, job dan outbox |
| Astra DB | Projection vektor terpisah per provider/model/version/scope; bukan sumber saldo/fakta otoritatif |
| Durable worker + Redis Streams | Outbox dispatch, retry/dead letter, schedule, reconciliation; SQL job tetap sumber state |
| S3-compatible | Paper privat, voice note, audio segment podcast opsional; tanpa binary di SQL |
| LiveKit / ElevenLabs / Xendit / Google | Media transport / TTS platform / pembayaran / identitas federasi |

```text
Client -> FastAPI -> PostgreSQL + outbox -> Redis -> worker -> Langflow workflows
             |                           |                  |-> Gemini embedding (admin)
             |-> Xendit <-> webhook       |                  |-> OpenAI embedding (admin)
             |-> credential broker       |                  +-> Astra projections
             +-> Langflow chat/Learn -> CallCraft -> internal domain API

Client <-> LiveKit <-> realtime worker -> STT -> LLM -> ElevenLabs
                              |            (direct streaming, no Langflow turn)
                              |-> CallCraft -> internal domain API
                              +-> persisted turns/usage/outbox -> background Langflow
```

## Jalur Eksekusi Utama

| Kebutuhan | Jalur | Pemilik state |
|---|---|---|
| Chat / Learn | Client → FastAPI → Langflow HTTP API → respons JSON atau SSE | Modul backend menyimpan pesan, status dan usage |
| Call / video / interupsi podcast | Client ↔ LiveKit ↔ realtime worker → provider langsung | Modul backend menyimpan transcript/checkpoint; worker mengatur media |
| Ingestion / facts / assessment / embedding | PostgreSQL + outbox → durable worker → Langflow HTTP API | SQL job menentukan retry, dedupe dan completion |
| Discovery / pemanggilan flow oleh agent internal | Agent → Langflow MCP → published flow | Backend tetap memvalidasi execution context dan scope |
| Function call pilihan AI | Langflow atau realtime worker → CallCraft → domain API | Modul pemilik menegakkan invariant dan transaksi |

Pesan asli dan transcript disimpan oleh backend/realtime worker **sebelum** pengolahan background. Langflow menghasilkan data turunan (summary, facts, assessment, chunks, vectors) dan menyimpan hasil melalui scoped runtime API/adapter; kegagalan ingestion tidak menghilangkan sumber percakapan. Astra hanya projection dari sumber canonical PostgreSQL.

Podcast memakai Langflow untuk persiapan dokumen/naskah dan pengolahan transcript. Playback, speaker switching, barge-in dan resume berjalan langsung di realtime worker.

## AI Runtime Snapshot

Backend resolves `plan_revision`, agent/persona version, LLM/STT model and credential refs, base URL policy, embedding profile, flow/prompt version, voice mapping, rate card and budgets before operation. Snapshot is immutable for a job/call/playback. Secrets are resolved just-in-time, never stored in the snapshot. Revocation dan model emergency-disable still cancel new invocations within running sessions.

Snapshot menyimpan credential record/config references yang tidak dapat di-redeem, bukan token sekali pakai. User operation mengunci plan policy; platform maintenance mengunci payer platform dan owner scope bila private. Shared admin ingestion memakai service principal tanpa user/plan/wallet. Runtime grant dan provider credential reference baru diterbitkan per attempt. Tidak ada synthetic user untuk menjalankan pekerjaan platform.

VIP resolves provider credentials from platform environment/secret manager. Advance resolves encrypted user credentials separately for LLM/STT. Background maintenance uses platform model/key; it must not unexpectedly consume BYOK or wallet after the foreground action. User-requested podcast generation and subjective TOEFL evaluation do use the job's selected plan. Allocation details: `billing-plans.md`.

## Langflow-centric Dengan Jalur Realtime Khusus

Langflow owns versioned non-realtime AI workflows, not just one giant flow. Realtime worker loads published persona/prompt/config artifacts validated during deployment; it never needs a Langflow run for every utterance. Context preparation/retrieval can occur before admission and via asynchronous refresh. If direct realtime retrieval is needed, use a bounded shared retrieval adapter with identical Astra scope policy and admin query embeddings. No unbounded retrieval or memory writes in audio hot path.

CallCraft remains the only execution route for AI-selected functions. Deterministic trusted service operations (persist message, reserve wallet, fetch authorized context, dispatch job) are not LLM function calls. They use narrow backend APIs/ports rather than pretending to be agent tools.

## Identity, Delivery, dan Secret

- Backend-issued access token resolves principal; Google identity is verified at login, not passed around as app authorization.
- Per-execution grant mengikat service/principal, owner bila private, resource, purpose, permitted tools, expiry dan request/trace ID. Signed context untuk hop CallCraft/domain API menambahkan issuer/audience; ordinary flow hanya menerima execution_ref. Prompt/client tidak dapat mengubah otoritasnya.
- Credential broker issues one-use, audience/purpose-bound references. Internal Langflow component or trusted realtime adapter resolves key over authenticated TLS. Delayed jobs obtain fresh references after reauthorization, not expired tokens copied from queue.
- Langflow run storage, traces and component diagnostics must demonstrably redact/exclude credentials; otherwise BYOK activation is blocked.
- Domain mutation + outbox commit atomically. Worker acknowledgment follows durable result. Duplicate delivery uses event/job keys; provider requests additionally require invocation identity and reconciliation.
- Persist input before invocation, output/status/usage afterward. Audio processing uses bounded persistence queues and periodic checkpoints; if durable storage/backpressure fails beyond configured budget, stop session explicitly rather than losing history/billing silently.

## Failure Semantics

No hidden provider/model/credential fallback. Dependency timeout returns typed failure with correlation. A partial answer records actual delivered/consumed usage and terminal state. Langflow ingestion outages accumulate durable work without delaying healthy realtime turns. Billing authorization and required credential/context must succeed before work starts. Kill switch blocks new operations and gracefully terminates affected work; refunds/settlement still run.

Data deletion tombstones canonical sources before vector/media deletion. Background workers check tombstones and source versions so delayed jobs cannot resurrect removed data. Tenant scope is single-user ownership, with admin-shared published agent/learning knowledge explicitly separate.
