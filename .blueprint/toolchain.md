# Toolchain dan Infrastructure Preparation

## Binding Commands

Python 3.12, uv 0.12.8, GNU Make, Docker Engine dengan Compose v2-compatible CLI. `requirements.txt` / `requirements-dev.txt` adalah direct dependency input; generated `requirements.lock` dan `requirements-dev.lock` adalah hasil resolver dengan hashes dan wajib version-controlled. Dev lock constrained oleh runtime lock agar test menggunakan versi runtime yang sama. Update lewat `make lock`, review diff/advisories, lalu `make sync check audit`. Tidak mengedit transitive pins manual.

`pyproject.toml` hanya konfigurasi tooling sampai application packaging diputuskan pada Phase 1. `custom_langflow_components/` berjalan pada dependency environment Langflow terpisah; excluded dari backend lint, bukan dianggap sudah tervalidasi. Tidak memasang Langflow server ke backend image. Provider plugins/STT/vision/PDF scanner tetap contract-gated sebelum dependency tambahan dikunci; core auth dependencies sudah termasuk.

## Local Setup

1. Isi `.env` sesuai environment contract. Copy `.env.deploy.example` ke `.env.deploy` untuk image references dan Compose host settings; jangan overwrite `.env` existing.
2. `make sync check` untuk Python environment dan preparation checks.
3. `make compose-check` memvalidasi Compose tanpa mencetak interpolated secrets.
4. `make infra-up` hanya menjalankan PostgreSQL/Redis internal. Tidak mem-publish port database ke host; aplikasi lokal perlu berjalan dalam network Compose atau dedicated reviewed local override untuk testing.
5. `make down` menghentikan stack tanpa menghapus volumes. Jangan gunakan `down -v` pada data yang ingin dipertahankan.

Tidak ada fake app/health endpoint. Dockerfile dan application profile sengaja membutuhkan `src/`, `alembic.ini`, `migrations/` nyata. `make build`, `migrate`, `up`, `lint`, `typecheck`, `test` baru berlaku setelah Phase 1. Mereka harus gagal jika artifact belum ada, bukan menampilkan sukses semu.

## Entry-point Contract untuk Phase 1

- API: `temanbule.api.main:app`, container port 8000; host port dari `.env.deploy`.
- Durable worker: `python -m temanbule.worker.main`.
- LiveKit worker: `python -m temanbule.realtime.main`; CLI/provider dispatch detail diadapter setelah contract spike.
- Migration: `python -m alembic upgrade head`; one-shot, tidak startup tiap replica.
- Deployment order: infrastructure healthy → one-shot migration sukses → API/workers → HTTP readiness verification. `make up` tidak otomatis migrate.
- `make up` hanya API/durable worker. Realtime explicit `docker compose --env-file .env --env-file .env.deploy --profile realtime up -d realtime-worker` setelah admission/drain settings lolos.

## Container dan Production Gates

Validasi persiapan: resolver/install Python 3.12 dan dependency compatibility berhasil; runtime audit setelah update cryptography ke 50.0.1 tidak menemukan known vulnerabilities. YAML lint berhasil. Compose schema valid dengan password sementara hanya pada proses `config --quiet` (tidak menjalankan container); `.env` aktual masih memerlukan `POSTGRES_PASSWORD`. Docker build aplikasi belum dapat diuji tanpa source/migration nyata.

Docker image multi-stage, hashed dependencies, non-root UID/GID, read-only runtime, bounded temporary storage, capability drop, no secret build context. SQL/Redis internal; application punya egress network untuk vendors. `.env.deploy.example` adalah local tag baseline; production wajib immutable reviewed digest untuk Python/uv/Postgres/Redis dan app image. CI actions perlu digest pinning sebelum protected production promotion.

Compose sekarang adalah topology baseline, bukan sertifikasi siap deploy. Phase 1 wajib menambahkan real healthchecks API/workers, resource/pids limits berdasarkan capacity plan, per-service least-privilege secret injection (baseline env_file masih shared), mounted signing key/KMS integration, worker consumer identity unik, retry/startup validation dan admission-drain readiness. Production tidak boleh dijalankan sebelum gates ini ditutup. Tidak menebak RAM/CPU budget VPS.

Postgres init credentials hanya efektif pada volume baru; perubahan password env tidak merotasi existing database. Redis AOF everysec dapat kehilangan recent delivery pada host failure; SQL outbox/job reconciliation wajib memulihkannya. Backup/PITR/off-host restore, Apache TLS/SSE settings, SMTP/network callbacks dan Langflow/CallCraft contract tests adalah release gates terpisah.

CI tahap persiapan menguji dependency integrity, YAML dan vulnerability audit. Setelah source ada, tambahkan lint/types/tests/migrations/build sebagai required checks; jangan skip missing tests sambil mengklaim aplikasi lulus CI. Live/billable integration hanya explicit opt-in, credential sandbox scoped, tidak pada fork PR.
