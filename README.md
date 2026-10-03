# TemanBule Backend

Backend pembelajaran bahasa Inggris bertenaga AI untuk pengguna Indonesia, dengan dua persona tutor — **Elean** dan **Willy** — dua mode penggunaan (**VIP** dan **Advance**), dan lima halaman frontend: Home, Chat, Call, Podcast, dan Profile.

> **Status:** Spesifikasi produksi aktif. Backend, migration, dan workflow Langflow sedang diimplementasikan. Dokumen otoritatif ada di [`.blueprint/`](.blueprint/README.md). Bahasa utama dokumentasi: Indonesia.

---

## Daftar Isi

- [Gambaran](#gambaran)
- [Fitur Utama](#fitur-utama)
- [Mode Penggunaan: VIP dan Advance](#mode-penggunaan-vip-dan-advance)
- [Arsitektur](#arsitektur)
- [Stack Teknologi](#stack-teknologi)
- [Struktur Proyek](#struktur-proyek)
- [Memulai](#memulai)
- [Konfigurasi Environment](#konfigurasi-environment)
- [Perintah Make](#perintah-make)
- [API](#api)
- [Keamanan](#keamanan)
- [Peta Dokumentasi Blueprint](#peta-dokumentasi-blueprint)
- [Roadmap Implementasi](#roadmap-implementasi)

---

## Gambaran

TemanBule adalah **modular monolith** Python 3.12 dengan **DDD pragmatis** dan **ports/adapters**. Satu codebase backend mempunyai tiga proses yang dapat dijalankan terpisah — **API** (FastAPI), **durable worker**, dan **realtime worker** (LiveKit) — sambil menjaga ownership domain tetap pada modul backend.

| Komponen | Tanggung jawab |
|---|---|
| **Backend (FastAPI + use cases)** | Auth, ownership, katalog/plan resolver, payment order Xendit, wallet ledger, reservasi, credential broker, CRUD/state, room admission, signed media, outbox |
| **Realtime worker** | LiveKit media, VAD/STT, streaming LLM langsung, frame vision, ElevenLabs, turn cancellation, podcast director, usage checkpoints |
| **Langflow** | Chat/Learn reasoning, RAG, PDF/chunk/script, ekstraksi fakta, assessment, TOEFL subjektif, dual embedding projection, background orchestration AI |
| **CallCraft** | Registry/schema, routing dan eksekusi seluruh tool/function call dari Langflow maupun realtime worker |
| **PostgreSQL 16** | Otoritas account, pesan, fakta, score, canonical chunks, ledger, payment, registry, job dan outbox |
| **Astra DB** | Projection vektor terpisah per provider/model/version/scope; bukan sumber saldo/fakta otoritatif |
| **Redis 7 Streams** | Delivery/consumer groups/retry; AOF, SQL reconciliation; bukan financial truth |
| **S3-compatible** | Paper privat, voice note, audio segment podcast opsional; tanpa binary di SQL |
| **LiveKit / ElevenLabs / Xendit / Google** | Media transport / TTS platform / pembayaran / identitas federasi |

```text
Client -> FastAPI -> PostgreSQL + outbox -> Redis -> worker -> Langflow workflows
             |                           |                  |-> Gemini embedding (admin)
             |-> Xendit <-> webhook       |                  |-> OpenAI embedding (admin)
             |-> credential broker        |                  +-> Astra projections
             +-> Langflow chat/Learn -> CallCraft REST -> JSON -> backend dispatcher

Client <-> LiveKit <-> realtime worker -> STT -> LLM -> ElevenLabs
                              |            (direct streaming, no Langflow turn)
                              |-> CallCraft REST -> JSON -> backend dispatcher
                              +-> persisted turns/usage/outbox -> background Langflow
```

## Fitur Utama

| Halaman | Kebutuhan backend |
|---|---|
| **Home** | Course → unit → lesson; video, reading, grammar exercise, quiz; progress; bantuan AI pada konten published dengan citation |
| **Chat** | Daftar/halaman chat, kategori, pilihan agent, teks dan voice note, riwayat, streaming SSE, vocabulary |
| **Call** | Voice call dan video call melalui LiveKit; model vision untuk video; barge-in menghentikan playback lama |
| **Podcast** | Upload paper PDF → ekstraksi/chunk → generate diskusi → edit judul → play ke room LiveKit dengan interupsi pengguna; satu orchestrator menjalankan dua peran Elean/Willy dengan suara berbeda, grounded pada paper |
| **Profile** | Edit akun, progres/assessment, vocabulary, riwayat TOEFL, plan VIP/Advance, top-up/ledger dan BYOK settings |

## Mode Penggunaan: VIP dan Advance

| Pekerjaan | VIP | Advance |
|---|---|---|
| Chat/Learn AI, LLM call/podcast, vision frame, TOEFL subjektif | Platform key, **debit wallet** sesuai rate card | **BYOK LLM**, tanpa debit wallet |
| Voice note/call/interupsi podcast STT | Platform key, **debit wallet** sesuai metering | **BYOK STT**, tanpa debit wallet |
| Podcast outline/script generation | Platform key, estimasi + reserve sebelum job | BYOK LLM, snapshot credential |
| **ElevenLabs TTS** (termasuk dua suara podcast) | Platform expense, **tanpa debit wallet** | Platform expense |
| **Embedding** kedua provider + query embedding | Platform expense, **tanpa debit wallet** | Platform expense |
| Background fakta/memori/assessment, indexing | Platform background model/key, **tanpa debit wallet** | Platform background model/key |

- **VIP:** pengguna membeli **token aplikasi** (bukan subscription), melalui Xendit. Rp1 = 1 token.
- **Advance:** pengguna menyimpan API key + base URL opsional dan memilih model katalog untuk **LLM dan STT** secara independen (BYOK). Baseline tanpa debit wallet.
- Home non-AI, akun, histori, dan saldo tidak membutuhkan saldo positif.
- VIP dan Advance adalah mode aktif **eksklusif** untuk pekerjaan baru; saldo tetap tersimpan saat berganti.
- TTS ElevenLabs dan seluruh embedding ditanggung platform — tidak mendebit wallet VIP dan tidak menggunakan BYOK.

## Arsitektur

Arsitektur target adalah **modular monolith, DDD pragmatis, ports/adapters**:

- **DDD:** modul mengikuti kemampuan bisnis, istilah domain dan pemilik data. Invariants (saldo, ownership, published version, lifecycle) ditegakkan oleh modul pemilik.
- **Ports/adapters:** use case memakai interface; PostgreSQL, Langflow, CallCraft dan SDK vendor dihubungkan melalui adapter.
- **Event-driven untuk background:** perubahan domain dan outbox disimpan atomik, lalu worker memproses event secara **at-least-once**. Ini bukan event sourcing; state otoritatif tetap tabel PostgreSQL.
- **PostgreSQL adalah sumber kebenaran;** Astra adalah projection yang dapat dibangun ulang.
- **Langflow** adalah orchestrator AI non-realtime eksternal, **bukan** pemilik aturan bisnis.
- **CallCraft** adalah satu-satunya jalur eksekusi untuk seluruh tool/function calling AI.
- **Tidak ada fallback** provider/model, harga, mock data, atau konfigurasi terselubung. Konfigurasi wajib yang kosong menggagalkan aktivasi feature secara eksplisit.

### Prinsip Penting

- Pesan/transcript asli disimpan backend **sebelum** ingestion. Langflow mengolah data turunan; SQL job tetap pemilik retry dan completion.
- Provider/model dipilih dari database aktif, bukan string bebas dari user. Seed provider aktif hanya `gemini` dan `openai`.
- Secrets tidak masuk blueprint, flow export, queue, logs, traces, atau database plaintext.
- **Outbox + durable worker** untuk pekerjaan penting; bukan `FastAPI BackgroundTasks`.
- Runtime snapshot immutable per job/call/playback: plan revision, agent/persona version, model & credential refs, embedding profile, flow/prompt version, voice mapping, rate card dan budgets.

## Stack Teknologi

| Stack | Tanggung jawab |
|---|---|
| **Python 3.12** | Bahasa utama backend |
| **FastAPI + Uvicorn** | Public/internal API, SSE, schema validation |
| **Pydantic + pydantic-settings** | Typed schemas, secret redaction, conditional feature config validation |
| **PostgreSQL 16 + SQLAlchemy async + asyncpg + Alembic** | Domain truth, ledger/locking, immutable versions, outbox, migrations |
| **Redis 7 Streams** | Delivery/consumer groups/retry |
| **HTTPX** | Typed adapters Langflow/CallCraft/Xendit/provider HTTP |
| **PyJWT[crypto], cryptography** | App JWT + execution context, Google ID token verify, envelope encryption/KMS |
| **Argon2id + OAuth client** | Password hashing dan Google authorization-code/PKCE |
| **Langflow** | Multiple AI workflows; custom components di `custom_langflow_components/` |
| **CallCraft API/MCP** | Semua eksekusi function AI, specs di `custom_callcraft_spec/` |
| **Astra DB + astrapy** | Separate Gemini/OpenAI vector spaces |
| **Gemini/OpenAI adapters** | LLM/STT capability selection, admin dual embedding |
| **LiveKit API/Agents** | Room/token/dispatch/audio/video, satu podcast director |
| **ElevenLabs adapter** | Platform TTS, Elean/Willy distinct voices; tanpa debit user |
| **Xendit adapter** | Hosted checkout/payment, webhook verification, reconciliation, refunds |
| **boto3 / S3** | Private PDF/media/cached segments |
| **OpenTelemetry** | End-to-end traces/metrics, redacted provider usage/cost |
| **uv, pytest, pytest-asyncio, testcontainers, ruff, mypy, schemathesis** | Reproducible dependency resolution, test, lint, type-check |
| **Apache + Docker Compose** | Host TLS/reverse proxy, private SQL/Redis, independently scaled workers |

## Struktur Proyek

```text
src/temanbule/
  api/                 # main:app, HTTP/SSE transport, middleware, composition root
  worker/              # durable worker entry point, dispatch/composition
  realtime/            # LiveKit entry point dan media lifecycle
  platform/            # settings, SQL unit-of-work, messaging, observability, crypto adapters
  modules/
    identity/          # Account, principal, session, akses
    catalog/           # Katalog/plan
    billing/           # Wallet, reservation, settlement
    ai_runtime/        # Snapshot, execution grant, flow registry, invocation coordination
    conversations/     # Pesan/transcript/sesi
    vocabulary/        # Vocabulary milik pengguna
    learning/          # Materi/progress
    assessments/       # Assessment, rubric, score
    knowledge/         # Fakta dengan provenance, canonical chunks, vector projection
    media/             # File lifecycle
    podcasts/          # Script/version, playback state
migrations/            # Alembic revisions
tests/                 # unit / integration / contract / e2e
.blueprint/            # Spesifikasi produksi otoritatif
custom_langflow_components/  # Custom Langflow components (env Langflow terpisah)
custom_callcraft_spec/       # CallCraft JSON specs
```

Setiap modul memisahkan `domain/` (invariants), `application/` (use cases/ports), `infrastructure/` (repositories, vendor adapters), dan `contracts/` (DTO/event schema publik) sesuai kebutuhan. Domain tidak mengimpor FastAPI, SQLAlchemy, Redis, atau SDK vendor.

## Memulai

### Prasyarat

- **Python 3.12**
- **uv 0.12.8**
- **GNU Make**
- **Docker Engine** dengan Compose v2

### Setup Lokal

1. **Isi `.env`** sesuai environment contract di [`.blueprint/environment.md`](.blueprint/environment.md). Copy `.env.deploy.example` ke `.env.deploy` untuk image references dan Compose host settings (jangan overwrite `.env` existing).

2. **Siapkan Python environment dan jalankan preparation checks:**
   ```bash
   make sync check
   ```

3. **Validasi Compose** (tanpa mencetak interpolated secrets):
   ```bash
   make compose-check
   ```

4. **Jalankan infrastruktur internal** (PostgreSQL/Redis saja; tidak mem-publish port database ke host):
   ```bash
   make infra-up
   ```

5. **Hentikan stack** (tanpa menghapus volumes):
   ```bash
   make down
   ```

> **Catatan:** Jangan gunakan `down -v` pada data yang ingin dipertahankan. Tidak ada fake app/health endpoint — `make build`, `migrate`, `up`, `lint`, `typecheck` memerlukan artifact aplikasi nyata. `make test` sudah dapat menjalankan tests adapter persiapan.

### Entry Points

| Proses | Command |
|---|---|
| API | `temanbule.api.main:app`, container port 8000 |
| Durable worker | `python -m temanbule.worker.main` |
| LiveKit worker | `python -m temanbule.realtime.main` |
| Migration | `python -m alembic upgrade head` (one-shot) |

**Deployment order:** infrastructure healthy → one-shot migration sukses → seed/provisioning → API/workers → HTTP readiness verification. `make up` tidak otomatis migrate/seed.

## Konfigurasi Environment

`.env` adalah konfigurasi lokal yang di-ignore; `.env.example` mendokumentasikan key backend tanpa credentials. Sumber konfigurasi terbagi tiga:

- **Env/secret manager:** service endpoints, platform credentials, crypto keys, bootstrap model/voice/collection binding, deployment limits.
- **PostgreSQL registries:** active provider/model capabilities, plan/catalog/pricing, flow/tool IDs, persona/prompt versions, vector profiles, runtime policy.
- **User settings:** encrypted BYOK, base URL yang lolos policy, LLM/STT selection. **User keys tidak disimpan ke `.env`.**

Feature gates default **false** untuk capability yang belum selesai. Blank required field adalah **error**, tidak pernah zero/magic default/silent disable. Lihat [`.blueprint/environment.md`](.blueprint/environment.md) untuk group dan dependency matrix lengkap.

## Perintah Make

| Command | Fungsi |
|---|---|
| `make lock` | Compile `requirements.txt`/`requirements-dev.txt` → hash-locked lock files |
| `make sync` | Buat venv Python 3.12 dan sync dari `requirements-dev.lock` |
| `make check` | `pip check` + YAML lint + Make dry-run |
| `make lint` | `ruff check src tests` |
| `make typecheck` | `mypy src` (strict mode) |
| `make test` | `pytest -m 'not live'` |
| `make audit` | `pip-audit` terhadap `requirements.lock` |
| `make compose-check` | Validasi Compose schema tanpa menjalankan container |
| `make infra-up` | Jalankan PostgreSQL/Redis internal saja |
| `make down` | Hentikan stack tanpa menghapus volumes |
| `make build` | Build image aplikasi (memerlukan source/migrations nyata) |
| `make migrate` | Jalankan Alembic migration (one-shot) |
| `make up` | Jalankan API + durable worker |

## API

Public prefix `/v1`; private resources selalu owner-scoped. Cursor pagination, request/trace correlation, optimistic version untuk update dan idempotency untuk mutasi wajib.

| Area | Contoh endpoint |
|---|---|
| Health | `GET /health/live`, `/health/ready` |
| Auth | `POST /v1/auth/register`, `/login`, `/refresh`, `/logout` |
| Google | `GET /v1/auth/google/start`, `/callback` |
| Profile | `GET/PATCH /v1/me/profile`, `/progress`, `/assessments`, `/facts` |
| Plan | `GET /v1/plans`, `GET/PUT /v1/me/plan` |
| Billing | `GET /v1/token-packages`, `/v1/me/wallet`, `POST /v1/billing/topups` |
| AI settings | `POST /v1/me/ai-credentials`, `PUT /v1/me/ai-selections/{llm\|stt}` |
| Chat | `POST/GET /v1/practice/sessions`, `POST .../{id}/messages` |
| Call | `POST /v1/calls`, `.../{id}:join-token`, `.../{id}:end` |
| Podcast | `POST/GET /v1/podcasts`, `.../{id}:generate`, playbacks |
| TOEFL | `GET /v1/toefl/tests`, `POST/GET /v1/toefl/attempts` |
| Jobs | `GET /v1/jobs/{id}`, `.../{id}:cancel`, `.../{id}:retry` |

Async generation mengembalikan **202** dengan job ID; ready/result tidak pernah dinyatakan saat acceptance. Error envelope stabil dengan kode eksplisit (`INSUFFICIENT_TOKENS`, dsb.), bukan raw vendor responses. Lihat [`.blueprint/api-events.md`](.blueprint/api-events.md) untuk kontrak lengkap, webhooks, dan streaming SSE/LiveKit data.

## Keamanan

- Argon2id password hashing; refresh token rotation dengan family reuse detection; access JWT asymmetric signing dengan issuer/audience/kid.
- Google OAuth authorization-code flow dengan state, nonce, PKCE S256, dan exact redirect allowlist. Tidak ada auto-link hanya karena email sama.
- BYOK dienkripsi dengan envelope encryption/KMS; plaintext tidak pernah di-log, dikembalikan, di-queue, atau disimpan Langflow.
- Validasi `base_url` kustom: HTTPS, host/port policy, DNS rebinding protection, private/link-local/metadata IP denial.
- Ownership dipaksakan pada application service/repository; tool allowlist dan authorization adalah kontrol di luar prompt.
- Rate limiting pada auth, Langflow run, tool execution, media upload, dan call creation.
- Public dan internal routes terpisah; `/internal/*` deny-by-default dengan service authentication.
- Secrets tidak masuk blueprint, flow export, queue, logs, traces, atau database plaintext.

Lihat [`.blueprint/security-operations.md`](.blueprint/security-operations.md) untuk data classes/retention, observability, retry policy, required tests, dan runbooks.

## Peta Dokumentasi Blueprint

Untuk memahami arah implementasi, mulai dari `architecture.md` → `backend-layout.md` → `langflow-flows.md` → `implementation-plan.md`. Dokumen lain menjadi referensi detail per capability.

| Dokumen | Isi |
|---|---|
| [`product-requirements.md`](.blueprint/product-requirements.md) | Fitur, batas scope, acceptance produk |
| [`architecture.md`](.blueprint/architecture.md) | Otoritas komponen dan jalur realtime/background |
| [`billing-plans.md`](.blueprint/billing-plans.md) | Token, tarif, reservasi, pembayaran, perpindahan plan |
| [`auth-provider-policy.md`](.blueprint/auth-provider-policy.md) | Email/password, Google OAuth, katalog, BYOK |
| [`realtime-podcast.md`](.blueprint/realtime-podcast.md) | Voice/video call dan podcast interaktif |
| [`postgresql-schema.md`](.blueprint/postgresql-schema.md) | Data relasional, constraint, state |
| [`astra-collections.md`](.blueprint/astra-collections.md) | Fan-out embedding Gemini/OpenAI dan retrieval |
| [`langflow-flows.md`](.blueprint/langflow-flows.md) | Workflow, input/output, prompt governance |
| [`callcraft-tools.md`](.blueprint/callcraft-tools.md) | Function calling dan kontrak argument |
| [`api-events.md`](.blueprint/api-events.md) | API, event, streaming, failure behavior |
| [`environment.md`](.blueprint/environment.md) | Kontrak konfigurasi `.env` / `.env.example` |
| [`backend-layout.md`](.blueprint/backend-layout.md) | Layout aplikasi, import/transaction boundaries |
| [`implementation-plan.md`](.blueprint/implementation-plan.md) | Urutan implementasi dan release gates |
| [`toolchain.md`](.blueprint/toolchain.md) | Dependency locks, Make commands, CI, Docker |
| [`execution-readiness.md`](.blueprint/execution-readiness.md) | Rules, milestone, Definition of Ready/Done |
| [`decision-register.md`](.blueprint/decision-register.md) | Keputusan terbuka, blocker per capability |
| [`security-operations.md`](.blueprint/security-operations.md) | Security baseline, observability, runbooks |
| [`docker-deployment.md`](.blueprint/docker-deployment.md) | Deployment VPS Ubuntu + Apache + Docker Compose |

## Roadmap Implementasi

| Phase | Fokus |
|---|---|
| **Phase 0** | Keputusan dan contract spikes (komersial, Xendit, models, embedding, Langflow, CallCraft, Google/email, realtime/TTS, produk/operasi) |
| **Phase 1** | Foundation dan Auth (modular layout, migrations, outbox/idempotency, email/Google auth) |
| **Phase 2** | Catalog, Agents, Plans dan Billing (BYOK, ledger/reservation, Xendit) |
| **Phase 3** | AI Control Plane dan Text Slice (Langflow/CallCraft registries, credential broker, dual embedding, retrieval) |
| **Phase 4** | Home dan Profile/TOEFL (published lessons, assessment, TOEFL scoring) |
| **Phase 5** | Private Media dan Voice Note (S3, STT, PDF sandbox) |
| **Phase 6** | Voice dan Video Call (LiveKit, barge-in, vision, usage rolling reserve) |
| **Phase 7** | Podcast (paper ingestion, script generation, Elean/Willy playback, interupsi) |
| **Phase 8** | Production Readiness (E2E, security, payment incident drills, KMS rotation, capacity, SLO) |

> Milestone **M3** (text vertical slice: registrasi → plan → top-up/BYOK → chat → vocabulary → history/usage) adalah checkpoint integrasi pertama, **bukan** pengurangan scope produk atau izin public launch.

---

> **Aturan mengikat:** Tidak ada fallback provider/model, harga, mock data, atau konfigurasi terselubung. Spesifikasi lengkap bukan bukti kesiapan produksi. Jika kontrak vendor tidak sesuai spesifikasi, dokumentasikan gap/ADR sebelum mengaktifkan capability terkait.
