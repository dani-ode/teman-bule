# Execution Readiness dan Milestone

Status: baseline persiapan implementasi. Milestone adalah gerbang verifikasi, bukan klaim fitur sudah selesai atau persetujuan produksi. Urutan fase tetap mengikuti `implementation-plan.md`; seluruh scope produk tetap mengikuti `product-requirements.md`.

## Aturan kerja mengikat

- `.agents/rules/public.md`: konfigurasi dinamis wajib eksternal; tanpa credential/endpoint/harga/limit hardcoded atau mock fallback runtime. Missing required configuration, invalid type dan unexpected state menghasilkan typed failure dengan correlation ID.
- `.agents/rules/skills.md`: spesifikasi dan pedoman proyek dipusatkan di `.blueprint/`, diindeks melalui `README.md`.
- `.agents/rules/langflow.md`: Langflow mengelola pemrosesan/workflow sesuai boundary `architecture.md`; custom components di `custom_langflow_components/`, kontrol workflow melalui MCP. Jalur realtime khusus mengikuti keputusan arsitektur yang sudah tertulis.
- `.agents/rules/callcraft.md`: seluruh AI-selected tool/function execution melalui CallCraft/MCP; JSON spec di `custom_callcraft_spec/`. CRUD deterministik memakai domain service sesuai `architecture.md`.
- Konstanta protokol/domain yang memang ditetapkan spesifikasi berbeda dari konfigurasi dinamis. Perubahan kontrak harus terversi; angka bisnis, timeout, kapasitas dan parameter deployment harus mempunyai sumber konfigurasi/policy yang disahkan.
- Test doubles hanya untuk pengujian terisolasi dan diberi label jelas; tidak membuktikan kompatibilitas vendor dan tidak boleh menjadi runtime fallback.

## Milestone dan bukti penerimaan

| Milestone | Hasil | Bukti wajib |
|---|---|---|
| M0 — Ready per ticket | Ticket memiliki keputusan, kontrak dan dependency yang cukup | Definition of Ready; blocker ditautkan ke `decision-register.md` |
| M1 — Foundation | API dan durable worker nyata, migrations, auth email/Google, audit, outbox, idempotency | Seluruh FND di `foundation-backlog.md` lulus; restart/replay/ownership dan restore foundation terbukti |
| M2 — Billing/catalog | Plan, catalog, encrypted BYOK, snapshots, ledger, reserve, Xendit dan reconciliation | Gates komersial/vendor tertutup; concurrency saldo, webhook ganda, unknown usage, redaction teruji |
| M3 — Text vertical slice | Registrasi/login → pilih plan → top-up VIP atau konfigurasi Advance → chat Elean/Willy → `vocabulary.save` → history/usage | Alur VIP dan Advance teruji; Langflow/CallCraft aktual; tool mutation/debit tidak ganda; dual projection, citation dan ownership sesuai Phase 3 |
| M4 — Full product | Home/Profile/TOEFL, media, voice/video, podcast | Exit Phase 4–7 terpenuhi per capability |
| M5 — Production release | Seluruh scope siap dioperasikan | Exit Phase 8, kebijakan disahkan, recovery/capacity/security evidence dan rollback terverifikasi |

M3 adalah checkpoint integrasi pertama, bukan pengurangan scope produk atau izin public launch. Advance tetap mengikuti syarat selection LLM/STT dalam `billing-plans.md`; text slice tidak boleh diam-diam melonggarkannya. TTS/embedding tetap biaya platform.

## Definition of Ready

Ticket boleh masuk coding jika:

1. Tujuan, requirement sumber, acceptance dan failure cases ditulis.
2. Dependency ticket selesai atau interface-nya sudah disepakati sehingga pekerjaan dapat diverifikasi independen.
3. Keputusan yang dibutuhkan tersedia; unresolved decision memblokir hanya ticket/capability yang bergantung padanya.
4. DTO/schema, state transition, owner scope, transaction boundary dan idempotency relevan sudah ditentukan sebelum handler.
5. Cara pengujian, kebutuhan infrastruktur dan budget untuk live/billable test diketahui.

## Definition of Done

- Schema/migration sesuai scope dibuat sebelum persistence/handler; constraints dan upgrade diuji pada PostgreSQL nyata.
- Strict types, lint dan meaningful tests lulus. Bukti mencantumkan command, environment/versi, revision sumber, hasil serta kasus yang belum diuji.
- Acceptance sukses, invalid input, forbidden owner, concurrency/replay, dependency failure dan recovery diuji sesuai jenis pekerjaan.
- Tidak ada secret di log, payload event, fixtures, flow export atau evidence. Error stabil dan correlated; exception tidak ditelan.
- Kontrak/artifact dan dokumentasi diperbarui bersama perubahan; incompatible change memakai versi baru.
- Deployment/migration/recovery yang terdampak mempunyai prosedur terverifikasi. Test yang dilewati atau mock-only vendor test tidak menutup integration gate.

## Template ticket dan evidence

```text
ID / milestone / status:
Tujuan dan requirement sumber:
Penanggung jawab pelaksanaan / reviewer:
Dependency ticket / decision IDs:
Modul dan kontrak yang disentuh:
Schema / state / ownership / transaction boundary:
Acceptance dan failure cases:
Perintah verifikasi / environment / budget bila live:
Bukti: revision, versi dependency/vendor, tanggal, hasil, fixture teredaksi:
Risiko terbuka dan batas pembuktian:
```

Status ticket: `planned → ready → in_progress → verified`, atau `blocked` dengan alasan dan dependency. `verified` memerlukan evidence; keberadaan file bukan bukti selesai. Penanggung jawab berbasis peran harus diisi nama sebelum ticket dikerjakan.

## Traceability awal

| Requirement | Milestone / ticket | Bukti yang dituntut |
|---|---|---|
| Missing config gagal eksplisit tanpa fallback | M1 / FND-02 | Startup invalid config; diagnostic teredaksi |
| Refresh rotation/reuse dan auth epoch | M1 / FND-07 | Refresh paralel, replay, logout/reset token lama |
| Domain write dan outbox atomic | M1 / FND-04, FND-05 | Crash boundary, duplicate delivery, recovery |
| Private resource owner-scoped | M1 / FND-03, FND-07; M3 | Cross-owner access dan composite relation rejection |
| Satu paid order satu top-up | M2 | Duplicate/out-of-order webhook dan concurrent reconciliation |
| Unknown usage tidak diretry/debit secara buta | M2 | Timeout setelah provider menerima request; reconciliation |
| TTS/embedding tidak mendebit wallet | M2–M3 | Settlement ledger dan payer attribution |
| Tool tidak mengklaim sukses palsu | M3 | Mutasi DB, audit dan tool result; vendor failure |
| Retry tidak menggandakan invocation/mutasi/debit | M3 | Request replay, SSE reconnect, worker restart |
| Dua projection tetap owner-scoped | M3 | Single-branch retry, deletion tombstone, cross-owner retrieval |

Matriks diperluas bersama ticket Phase 2–8 sebelum fase tersebut dimulai. Evidence disimpan sebagai dokumen teredaksi di `.blueprint/`; executable tests/fixtures berada pada direktori test/artifact terkait.
