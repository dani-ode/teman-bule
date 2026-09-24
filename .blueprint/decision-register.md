# Decision Register

Status awal seluruh baris: **open**. Peran di bawah adalah tanggung jawab yang perlu ditunjuk, bukan tanda approval. Tidak ada harga, model, batas, SLA atau kontrak vendor yang dianggap final melalui dokumen ini.

## Register keputusan

| ID | Keputusan/output wajib | Penanggung jawab | Menghambat paling awal | Status |
|---|---|---|---|---|
| DEC-01 | Packaging backend, import/install strategy untuk src layout dan Docker, batas dependency modul | Backend lead | FND-01 | open |
| DEC-02 | Settings per service/feature, signing algorithm/key lifecycle, KMS/M2M, auth TTL/cookie/CSRF/Argon2 policy dan rate limits | Backend + operasi | FND-02, FND-07 | open |
| DEC-03 | Isolation/locking per transaksi, idempotency hash/replay/expiry, job lease/retry/dead letter dan fencing policy | Backend lead | FND-03–05 | open |
| DEC-04 | Execution grant storage, expiry, consumption/replay, revocation, audience dan service identity binding | Backend + integrasi | FND-09, runtime gateway | open |
| DEC-05 | Google registered callbacks, origin/redirect allowlist, SMTP/domain/TLS dan delivery fixtures | Backend + operasi | Aktivasi email/Google FND-06, FND-08 | open |
| DEC-06 | Harga paket, currency/minor unit, token scale/rate card, refund/dispute, baseline biaya Advance | Pemilik produk + billing | M2 billing aktif | open |
| DEC-07 | Produk/API Xendit, webhook auth/status/dedupe, timeout lookup, refund dan sandbox/live fixtures | Integrasi pembayaran | M2 checkout aktif | open |
| DEC-08 | Model VIP/BYOK/background, capabilities, metering/cancel/unknown usage dan katalog terverifikasi | Integrasi AI | M2 catalog aktif; M3 invocation | open |
| DEC-09 | Gemini/OpenAI embedding model/revision/dimension/task/normalization; pasangan collection per scope aktif, sepuluh untuk produk lengkap, dan reindex | Integrasi AI | M3 indexing/retrieval sesuai scope aktif | open |
| DEC-10 | Versi deployment Langflow; target Workflow API v2 + developer API flag, migrasi adapter/config v1; sync/stream/background, restart recovery/cancel, kemampuan project vs management MCP, component/import/export dan secret non-persistence | Integrasi AI + operasi | M3 flow aktif | open |
| DEC-11 | CallCraft format/auth/MCP, binding endpoint, trusted context, idempotency/errors | Integrasi AI | M3 tool aktif | open |
| DEC-12 | DTO/JSON Schema rinci, payload/list/context limits, SSE replay retention, timeout; progress first-write/enum, fact policy dan tool allowlist | Backend + produk + frontend | Kontrak fitur terkait, M3 tool/flow | open |
| DEC-13 | Persona Elean/Willy, artifact/version, voice binding; rubric dan score bounds TOEFL | Pemilik produk + integrasi AI | M3 persona; M4 TOEFL/TTS | open |
| DEC-14 | ElevenLabs model/voices, LiveKit/plugins, STT stream, vision frames, concurrency/latency/cancel | Integrasi realtime | Phase 6 | open |
| DEC-15 | Podcast target/max/extension, call quotas, media/PDF limits, scanner, OCR policy | Produk + operasi | Phase 5–7 sesuai capability | open |
| DEC-16 | Retention/consent/deletion, SLO/RPO/RTO, capacity/budget/domain, backup dan rollout topology | Produk + operasi | Kebijakan data fitur terkait; FND-10 restore; M5 release | open |
| DEC-17 | Bootstrap manifest/CLI, natural-key conflict policy, seed metadata/title/order, registry lifecycle; Astra naming, metric/index policy dan resumable provisioning sesuai database-bootstrap.md | Backend + integrasi AI | FND-03 mekanisme bootstrap; M2 seed; M3 vector activation | open |

Scope tambahan dari audit: DEC-02 mencakup access-token session-family revocation/cache; DEC-03 job attempts dan unknown-outcome reconciliation; DEC-04 service-principal grants tanpa plan; DEC-09 document/query task types, metadata array filter dan metric; DEC-10 envelope v3 dan migrasi RuntimeContext untuk shared ingestion. Semuanya tetap open sampai ada bukti, bukan otomatis approved oleh koreksi dokumen.

## Lifecycle dan keputusan arsitektur

`open → investigating → proposed → approved`; keputusan dapat `superseded` dengan referensi pengganti. Proposal tidak menutup blocker. Satu baris besar dapat dipecah menjadi ID turunan agar keputusan yang sudah siap tidak menunggu capability lain; parent selesai hanya setelah seluruh scope tertutup.

Untuk setiap keputusan yang diajukan/disahkan, catat di `.blueprint/`:

```text
Decision ID / judul / status:
Konteks dan requirement/rules sumber:
Pilihan yang dinilai serta konsekuensi:
Keputusan dan alasan:
Pemilik / reviewer / penyetuju bernama / tanggal:
Scope dan ticket/capability terdampak:
Bukti vendor/test/policy teredaksi dan versinya:
Perubahan kontrak/config/migration yang diperlukan:
Risiko, rollback dan kondisi peninjauan ulang:
```

Perubahan boundary Langflow/CallCraft, ownership data, biaya atau keamanan harus memperbarui blueprint sumber dan artefak terkait dalam perubahan yang sama. Jika vendor tidak mendukung kontrak, catat gap dan revisi keputusan; jangan menambahkan fallback tersembunyi.

## Contract spikes sebelum aktivasi

| Spike | Decision IDs | Bukti minimum |
|---|---|---|
| SPK-01: Langflow → CallCraft → backend | DEC-04, DEC-10, DEC-11 | Binding endpoint asli, context tidak dapat dioverride prompt, forbidden scope/owner, replay/conflict, error mapping, MCP dan import/export |
| SPK-02: Streaming dan credential broker | DEC-04, DEC-08, DEC-10 | Cancel/partial output, timeout, revoked/expired reference, secret tidak persisten pada run/log/trace/export |
| SPK-03: Payment lifecycle | DEC-06, DEC-07 | Sandbox checkout/webhook/lookup/refund sesuai produk; double/out-of-order delivery dan timeout sesudah provider menerima request |
| SPK-04: Model metering | DEC-08 | Identifier/capability valid; usage sukses/partial/cancel, provider request identity dan unknown-usage recovery |
| SPK-05: Dual embeddings | DEC-09 | Dimension/task/normalization valid, filter owner, single-branch retry dan generation/reindex |
| SPK-06: Auth delivery | DEC-02, DEC-05 | Callback exact, state/nonce/PKCE/replay/link, SMTP delivery dan redaction |
| SPK-07: Realtime/media | DEC-14, DEC-15 | Versi plugin, dua suara, barge-in/cancel, frame policy dan latency/concurrency terukur |

Setiap spike mencatat versi deployment/SDK, tanggal, prosedur reproduksi, fixtures teredaksi, hasil/gap, budget aktual dan keputusan lanjut. Pengujian billable hanya dengan budget eksplisit. Fixture sintetik berguna untuk unit test tetapi tidak menjadi bukti respons vendor aktual. Sandbox pass tidak otomatis menutup live production gate.
