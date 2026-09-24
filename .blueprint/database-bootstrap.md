# Database Bootstrap dan Provisioning

Status: kontrak implementasi, belum ada migration/seed runner atau provisioning Astra yang terverifikasi. Bootstrap dijalankan operator terotorisasi, bukan otomatis setiap API startup.

## Empat Tahap

| Tahap | Hasil | Batas |
|---|---|---|
| Migration | Tabel, constraint dan index PostgreSQL | Sesuai slice; tidak memanggil vendor |
| Seed referensi | Identitas bisnis stabil dan metadata yang disahkan | Tidak membuat pengguna, saldo, percakapan atau hasil belajar contoh |
| Provisioning | Registry model/flow/tool/voice dan Astra collections tervalidasi | ID vendor aktual, konfigurasi eksplisit, tanpa secret di manifest |
| Publication dan ingestion | Konten/persona/rubric terversi; canonical chunks dan vectors | Melalui use case/outbox/Langflow, bukan insert vector contoh |

## Manifest Seed

Manifest terversi memuat `manifest_version`, checksum, environment target, required migration revision dan daftar entry dengan natural key. Runner mendukung preview/diff dan apply eksplisit; nama CLI ditetapkan pada DEC-17 sebelum implementasi. Hasil dicatat pada `bootstrap_runs` dan audit tanpa secret.

| Data | Natural key / identitas awal | Aturan aktivasi |
|---|---|---|
| Plans | `plans.code`: `vip`, `advance` | Policy revision disahkan sebelum selection aktif |
| Provider | `provider_catalog.code`: `gemini`, `openai` | Endpoint/policy tervalidasi; aktifnya provider tidak otomatis mengaktifkan model |
| Agents | `agents.code`: `elean`, `willy` | Identitas dapat dibuat draft; persona published untuk chat, voice binding terverifikasi untuk audio |
| Practice categories | `practice_categories.code`: `daily_conversation`, `grammar`, `pronunciation`, `job_interview`, `travel`, `free_talk` | Title/sort order eksplisit dalam manifest produk |
| Models | `(provider, identifier, revision)` | Capability, protocol, limits dan metering diverifikasi; tidak menebak identifier |
| Flow/tool bindings | `(environment, purpose, flow_version)` / `(environment, name, schema_version)` | Artifact immutable, ID vendor aktual, schema/allowlist dan gate integrasi lulus |
| Embedding profiles | `(provider, model_id, model_revision, generation)` | Dimension, document/query task type dan normalization terverifikasi |
| Vector collections | `(environment, scope, profile)` | Physical collection dan opsi index/metric cocok; readiness dibuktikan |
| Token packages / rate cards | `(package_code, revision)` / rate-card version | Harga, unit saldo dan tarif disahkan; tidak ada harga dummy |
| Platform ledger accounts | `(account_type, asset)` untuk akun platform | Asset/account taxonomy disahkan billing; saldo awal nol, tanpa journal top-up sintetis |
| Lessons / agent knowledge / TOEFL | Published resource + revision | Konten/rubric disahkan; ingestion sesudah commit publication |

Wallet pengguna dibuat melalui use case lifecycle account/billing, bukan seed global. Tidak ada default admin/password; provisioning operator memakai identitas terotorisasi.

## Idempotency dan Revision

- Seed identik menghasilkan no-op. Natural key menemukan ID existing; jangan mengganti ULID/FK historis pada rerun.
- Manifest version sama dengan checksum berbeda ditolak. Manifest baru boleh mengubah metadata mutable melalui expected version; published policy/persona/rate/model configuration dibuat sebagai revision baru.
- Konflik data existing ditampilkan sebagai diff dan gagal eksplisit; tidak overwrite diam-diam, tidak truncate atau delete koleksi/tabel saat bootstrap.
- Serialize apply per environment dengan lock; catat attempt/status. Commit satu unit registry beserta audit secara atomik. Network provisioning tidak menahan transaksi SQL.
- Secret hanya di secret manager/environment terotorisasi; manifest memuat referensi konfigurasi non-secret. Provider availability tidak disimpulkan dari adanya API key.

## Provisioning Astra

1. Tentukan profile dan binding collection di registry berstatus `staged`: environment, scope, model/revision, generation, dimension, similarity metric dan metadata indexing policy.
2. Gunakan nama deterministik dari manifest: environment + scope + provider + profile/generation identifier. Batas karakter/panjang mengikuti Astra yang diverifikasi. ENV collection names adalah bootstrap bindings, bukan pengganti registry generation.
3. Buat collection kosong atau inspeksi collection existing. Semua opsi harus cocok; mismatch gagal dan memerlukan generation/collection baru. Jangan drop/recreate otomatis.
4. Explicit vectors dihasilkan Langflow/trusted embedding adapter; tidak sekaligus mengaktifkan vectorize server-side untuk dokumen yang sama. Verifikasi create/read/upsert/query/delete dan filter menggunakan fixture terisolasi yang dibersihkan.
5. Aktifkan binding untuk menerima indexing setelah provisioning lulus. Untuk reindex, query routing tetap pada generation lama hingga data generation baru diverifikasi dan activation atomik selesai.
6. Publish konten canonical melalui backend; outbox membuat pekerjaan Langflow. SQL menyimpan projection status, Astra menyimpan vector. Tidak ada transaksi lintas PostgreSQL–Astra; recovery melalui idempotency/reconciliation.

Target produk lengkap adalah lima scope × dua provider = sepuluh collection query-active. Provisioning bertahap boleh hanya scope fitur yang sedang diaktifkan, tetapi tiap scope mempunyai pasangan Gemini/OpenAI. Generation reindex dapat menambah collection sementara. Foundation tanpa AI tidak membutuhkan Astra.

## Urutan dan Acceptance

Foundation: migration identity/reliability + mekanisme bootstrap. Phase 2: plan/provider/catalog/billing dan identitas/persona agent yang disahkan. Phase 3: kategori, flow/tool bindings dan pasangan collection `user_memory`/`agent_knowledge`. Phase 4: `learning_content`/`toefl_feedback`. Phase 7: `podcast_sources` (persiapan parsing pada Phase 5).

Uji fresh database, rerun no-op, checksum conflict, concurrent apply, rollback SQL, network timeout sesudah collection dibuat, mismatch dimension/metric/index, single-branch retry, cross-owner query dan reindex recovery. Bukti live vendor terpisah dari test doubles. `make migrate` hanya migration; belum ada perintah seed/provision yang diimplementasikan.
