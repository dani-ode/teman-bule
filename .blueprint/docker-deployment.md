# TemanBule - Docker Deployment Specification

> **Status:** Target deployment specification; implementation and capacity verification pending
> **Versi:** 3.0.0
> **Tujuan dokumen:** Menetapkan deployment backend TemanBule pada VPS Ubuntu menggunakan Docker Compose dan Apache HTTP Server.

---

## 1. Keputusan Deployment

1. Production dijalankan pada satu VPS Ubuntu dengan Docker Engine dan Docker Compose v2.
2. Apache HTTP Server berjalan di host Ubuntu sebagai reverse proxy dan TLS termination untuk domain publik.
3. Backend dijalankan melalui Docker Compose dengan service `api`, `worker`, `realtime-worker`, `postgres`, dan `redis`; migration one-shot terpisah.
4. Public user route dan machine-to-machine route memakai policy berbeda. `/internal/v1/tools/*` menerima CallCraft dengan service authentication plus signed execution context; runtime/flow-data/credential routes hanya untuk trusted service adapters sesuai audience/scope. Semua internal route deny-by-default dengan request/body limit dan network allowlist atau mTLS bila didukung.
5. PostgreSQL dan Redis tidak boleh membuka port ke internet atau host. Astra DB, CallCraft MCP, provider AI, STT/TTS, LiveKit, object storage, dan identity provider tetap merupakan layanan external sesuai konfigurasi aplikasi.
6. Docker Compose yang sama digunakan untuk local development dan production dengan file environment yang berbeda. Production tidak memakai bind mount source code atau hot reload.
7. Langflow, CallCraft, Astra DB, LiveKit, Google OAuth, Xendit, SMTP, S3, Gemini/OpenAI dan ElevenLabs external. Email/password dan app sessions dikelola backend. Menjalankan Langflow pada stack ini memerlukan ADR/capacity plan terpisah.

---

## 2. Artefak yang Wajib Tersedia

| Artefak | Fungsi |
|---|---|
| `Dockerfile` | Membangun image backend untuk `api` dan `worker`. |
| `requirements.txt` | Daftar direct dependency Python dan compatible ranges untuk proses resolution. |
| `requirements-dev.txt` | Runtime requirements ditambah test, lint, type-check, dan contract-test tooling; tidak dipasang ke runtime image. |
| `requirements.lock` | Hasil resolution `uv` yang terversi, pinned, dan memiliki hashes; menjadi input build production. |
| `compose.yaml` | Mendefinisikan service production: `api`, `worker`, `realtime-worker`, `postgres`, dan `redis`. |
| `compose.dev.yaml` | Override development opsional untuk bind mount, hot reload, dan port debugging. Tidak dipakai production. |
| `.env.example` | Daftar variable yang diperlukan tanpa nilai secret. |
| `.dockerignore` | Mengecualikan `.env`, credential, cache, artifact, dan metadata Git dari build context. |
| `deploy/apache/temanbule.conf` | Target VirtualHost Apache untuk HTTPS API/SSE dan policy internal routes. |
| `deploy/systemd/temanbule.service` | Unit systemd untuk menjalankan `docker compose up -d` saat VPS boot dan menghentikannya secara bersih. |

## 3. Service dan Data

| Service | Tanggung jawab | Persistensi |
|---|---|---|
| `api` | FastAPI control plane/SSE/webhooks, bukan audio hot path. Mendengarkan pada `127.0.0.1:<port-api>` host. | Tidak ada |
| `worker` | Consumer Redis Streams, ingestion, retry, dan rekonsiliasi. Memakai image yang sama dengan `api`, command berbeda. | Tidak ada |
| `realtime-worker` | LiveKit dispatch, direct STT/LLM/TTS, video frames, podcast director, lease/fencing/checkpoints; independent concurrency limit. | SQL checkpoints; ephemeral media buffers |
| `postgres` | System of record dan transactional outbox. | Named volume Docker pada VPS |
| `redis` | Redis Streams, consumer groups, retry scheduling, dan delivery durable. | Named volume dengan AOF |

- `api` dan `worker` wajib restart otomatis kecuali saat kegagalan konfigurasi yang membutuhkan intervensi operator.
- Migration database dijalankan sebagai perintah one-shot yang terdokumentasi sebelum aplikasi baru dijalankan. Migration tidak dijalankan otomatis oleh setiap container `api`.
- Database dan Redis hanya berada pada Docker network internal. Port tidak memakai `ports:` ke host.
- Named volume PostgreSQL dan Redis wajib masuk ke prosedur backup VPS. Volume tidak boleh dihapus oleh perintah deployment rutin.

## 4. Apache Reverse Proxy

- Apache memakai `mod_proxy`, `mod_proxy_http`, `mod_ssl`, dan `mod_headers`; `mod_proxy_wstunnel` hanya jika endpoint WebSocket aplikasi ditambahkan.
- VirtualHost HTTP hanya mengalihkan seluruh traffic ke HTTPS.
- VirtualHost HTTPS meneruskan HTTP API dan chat SSE ke `http://127.0.0.1:<port-api>`. Media/signaling LiveKit langsung ke layanan LiveKit eksternal; backend belum menetapkan endpoint WebSocket aplikasi. Proxy WebSocket hanya ditambahkan bila ada kontrak endpoint yang nyata.
- Apache memiliki policy deny-by-default untuk seluruh `/internal/`, dengan allowlist terpisah per tools/runtime/flow-data/credentials sesuai service caller. Rate/body limit, application service authentication dan audience-bound execution context/reference tetap wajib.
- Apache menangani sertifikat TLS, termasuk renewal otomatis. Container aplikasi tidak menyimpan private key TLS.
- Header `X-Forwarded-For`, `X-Forwarded-Proto`, dan `Host` diteruskan setelah sanitasi proxy. Trusted proxy address mengikuti peer Apache yang benar-benar terlihat dari container (dapat berupa Docker gateway, bukan loopback); verifikasi pada deployment, jangan trust semua sumber.
- Timeout dan buffering proxy SSE harus dikonfigurasi eksplisit sesuai deadline/reconnect aplikasi; timeout media LiveKit dikelola pada runtime LiveKit terpisah.
- Apache access/error log dan log aplikasi harus dipantau serta tidak boleh memuat API key, authorization token, maupun isi percakapan sensitif.

## 5. Image dan Konfigurasi

- Dockerfile memakai multi-stage build dan memasang dependency dari `requirements.lock`, bukan melakukan floating resolution dari `requirements.txt` saat production build. Image runtime tidak memuat source control metadata, test tooling, `.env`, atau credential.
- Container aplikasi berjalan sebagai user non-root dan menangani `SIGTERM` untuk graceful shutdown.
- Image deployment diberi tag versi atau commit SHA. Deployment tidak menggunakan tag `latest`.
- File `.env` production disimpan hanya pada VPS, dimiliki user deployment, dengan permission minimum (`0600`), dan tidak di-commit.
- Compose boleh membaca `.env` production untuk konfigurasi service. Nilai tidak boleh dicetak dalam log deployment atau dikirim ke image build.
- BYOK pengguna tidak pernah menjadi Docker environment variable. Ia tetap dikelola sebagai secret aplikasi sesuai `architecture.md` dan `security-operations.md`.
- Startup `api` dan `worker` wajib memvalidasi konfigurasi kritis dan gagal secara jelas tanpa membocorkan secret.

## 6. Operasi VPS

1. Host menjalankan pembaruan keamanan Ubuntu, firewall, Docker Engine, Docker Compose, dan Apache.
2. Firewall hanya membuka SSH yang dibatasi, HTTP (80), dan HTTPS (443). Port aplikasi, PostgreSQL, dan Redis ditutup dari publik.
3. Deployment menarik atau membangun image ber-tag, menjalankan migration satu kali, lalu menjalankan `docker compose up -d`.
4. Backup terjadwal mencakup dump PostgreSQL dan file/volume yang memang menyimpan data lokal. Backup diuji melalui restore berkala.
5. Operator memeriksa health endpoint `api`, status `docker compose ps`, log container, kapasitas disk volume, dan masa berlaku sertifikat TLS setelah deployment.

## 7. Acceptance Criteria

1. Request HTTPS publik mencapai `api` melalui Apache tanpa membuka port API ke internet.
2. Chat SSE berfungsi melalui Apache tanpa buffering/timeout yang merusak stream; koneksi LiveKit langsung diverifikasi terpisah pada fase realtime.
3. PostgreSQL dan Redis tidak dapat diakses dari jaringan publik maupun host port.
4. Restart `api`, `worker`, atau Redis tidak menghilangkan data PostgreSQL atau event/job durable; duplicate delivery tetap idempoten.
5. Reboot VPS mengembalikan stack Compose melalui systemd dan Apache dapat kembali meneruskan traffic setelah aplikasi sehat.
6. Image dan file konfigurasi deployment tidak memuat plaintext credential di repository.
7. Backup PostgreSQL dapat direstore pada environment terisolasi.

## 8. Keputusan Terbuka

Media WebRTC mengalir langsung client ↔ LiveKit, tidak melalui Apache API proxy. Firewall egress mengikuti dokumentasi LiveKit/provider yang diverifikasi; kebutuhan TURN/UDP bukan ditangani endpoint SSE. Webhook Xendit/LiveKit memiliki public route terpisah dari internal tool routes dengan vendor authentication.

Drain realtime worker menghentikan admission, menjaga sesi hingga grace/deadline, checkpoint dan settle usage sebelum shutdown; fencing mencegah double director. Backup production memerlukan off-host encrypted backup + WAL/PITR sesuai RPO, restore drill, dan replay outbox/inbox setelah recovery. Satu VPS adalah failure domain tunggal; SLA high availability memerlukan ADR/topologi tambahan, bukan klaim HA dari Compose.

1. Domain production, strategi DNS, dan penerbitan/renewal sertifikat TLS.
2. Lokasi penyimpanan dan retensi backup PostgreSQL/media.
3. Resource limit berdasarkan hasil load test dan target SLO yang disetujui.
