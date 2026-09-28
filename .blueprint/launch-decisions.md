# Keputusan peluncuran — 2026-09-28

Pemilik keputusan: CTO/pemilik produk (pengguna). Penyusun: coding agent.
Arahan eksplisit CTO: Advance gratis biaya platform; Rp1 = 1 token aplikasi VIP;
target pembayaran Xendit v3; TTS `eleven_v3`; Langflow Workflow API v2.
Angka dan kebijakan lain di bawah adalah **baseline rekomendasi implementasi**,
bukan bukti vendor, persetujuan legal, atau klaim fitur sudah aktif.
Parent DEC tetap investigating sampai kontrak, publication registry, dan acceptance
terkait selesai. Tidak ada nama reviewer/approval yang direkayasa.

## DEC-06 — komersial

### Paket dan saldo

| Kode paket rev 1 | Harga IDR | Token aplikasi |
|---|---:|---:|
| vip_starter | 25.000 | 25.000 |
| vip_regular | 50.000 | 50.000 |
| vip_plus | 100.000 | 100.000 |
| vip_max | 250.000 | 250.000 |

Minimum Rp25.000 mengurangi dampak fixed payment fees pada transaksi kecil.
MVP hanya empat paket tetap; custom top-up ditunda. Bila dibuka: step Rp5.000,
minimum Rp25.000, maksimum Rp250.000/transaksi. Tanpa bonus token, subscription,
atau expiry saldo. `currency=IDR`, `amount_minor` mengikuti konvensi aplikasi
1 unit = Rp1, `display_scale=1`, 1 integer wallet unit = 1 token aplikasi.
Ini konvensi ledger aplikasi, bukan klaim exponent ISO 4217 IDR.
Biaya pembayaran ditanggung platform; nominal akhir harus ditampilkan sebelum bayar.
Paket/rate disimpan sebagai immutable DB revision, bukan dibaca dari environment.

### Rate card dan margin

Token aplikasi **bukan** token model. Harga model tidak dapat ditetapkan dari
identifier `gemini-3.5-transcribe`/`gemini-3.8-flash` yang belum terverifikasi.
Kedua model tidak boleh dipublish sebagai model billable sampai probe tersedia.
Kebijakan rate: multiplier awal 2x biaya provider pada kurs anggaran Rp17.000/USD;
review kurs mingguan dan tarif bulanan, revision baru hanya untuk invocation baru.
Target contribution margin setelah TTS, embeddings, gateway, dan compute ≥30%.
2x merupakan rekomendasi awal, bukan jaminan margin.

Untuk harga vendor `p` USD per sejuta input/output tokens, published rate per
sejuta = `p × 17000 × 2` wallet units, memakai Decimal/rational, bukan float.
Contoh perhitungan **bukan tarif vendor**: p input 0,30 / output 2,50 menghasilkan
10.200 / 85.000 wallet units per sejuta; 1.000 input + 500 output = ceil(52,7) = 53.
STT memakai audio_ms atau audio token sesuai kontrak terverifikasi; cached input
terpisah dari uncached, thinking termasuk output jika ditagih vendor, tidak double count.
TTS/embedding tetap zero debit eksplisit sesuai kontrak produk; ukur COGS terpisah.
Tarif numerik live per model masih menunggu harga dan metering akun aktual.

### Advance

Gratis biaya platform, tanpa minimum top-up atau saldo wallet; tidak bergantung
pada checkout Xendit. LLM/STT memakai kredensial pengguna sesuai capability.
Provider API key bukan saldo: tidak semua vendor menyediakan balance endpoint.
Keberhasilan verifikasi key tidak menjamin kuota berikutnya. Tangani quota exhausted,
invalid/revoked key, dan rate limit secara berbeda; hentikan pekerjaan dengan pesan
jelas saat kuota habis, tanpa fallback key platform dan tanpa debit VIP.
TTS/embedding/background tetap platform-funded. Usulan fair use awal untuk kedua
plan: satu sesi realtime/user, 30 menit/sesi, 60 menit/hari, dua podcast generation/hari.
Limit harian perlu admission counter; env saat ini hanya mengatur batas sesi/kapasitas.
Jika biaya melewati anggaran, kurangi admission yang diumumkan, jangan menambah fee diam-diam.

### Refund/dispute

Duplikasi pembayaran dan kegagalan fulfillment: refund penuh melalui metode asal.
Permintaan saldo belum terpakai: maksimum 7 hari sejak bayar; hold jumlah token
sebelum refund; proporsional Rp1/token. Saldo terpakai tidak direfund otomatis,
kecuali koreksi layanan/hak konsumen yang berlaku. Target proses internal 5 hari kerja,
settlement vendor terpisah. Chargeback dicatat sebagai dispute, tidak membuat wallet
negatif. Ketentuan pajak/invoice dan hak konsumen harus direview sebelum penjualan.

## DEC-07 — Xendit

Target CTO adalah integrasi pembayaran v3. UX pilihan: hosted checkout satu kali,
bukan penyimpanan kartu atau recurring. Dokumentasi resmi Payment Session menjelaskan
`session_type=PAY`, `mode=PAYMENT_LINK`, URL `payment_link_url`, dan webhook
`payment_session.completed`. Ini berbeda dari Invoice `/v2/invoices`.
Angka v3 pada Payment Requests tidak berarti endpoint hosted Invoice `/v3/invoices`
atau header `x-api-version: v3` tersedia.

**Gap aktual:** adapter repository hanya INVOICE v2 dengan header version dari config;
webhook/lookup/refund harus dipetakan ulang untuk produk yang dipilih. Pertahankan
nilai lokal lama sebagai konfigurasi legacy, bukan hasil verifikasi v3. Template
product/version dibiarkan kosong sampai kontrak endpoint + header bertanggal dari
akun Xendit diperoleh. Jangan aktifkan billing v3 hanya dengan mengganti env.
Server auth Basic secret-key/empty-password; target webhook callback token
`x-callback-token` (constant-time compare), exact merchant/reference/amount/currency
dan authenticated lookup sebelum kredit. Verifikasi bentuk event aktual di sandbox.
Return URL hanya UX; webhook/lookup + journal menjadi otoritas saldo. Expiry 1 jam.
Kasus wajib: duplicate/out-of-order, timeout accepted, late paid, refund, wrong amount.

## DEC-08/09 — model dan embedding

LLM/STT/background lokal belum dibuktikan tersedia pada akun. Probe metadata model
dulu; probe generation/metering memakai controlled budget. Jangan mengganti model
diam-diam atau menyatakan model valid dari namanya. BYOK catalog hanya model dengan
protocol/capability/metering yang benar-benar didukung adapter.

Pilihan awal: Gemini `gemini-embedding-001` **768d** + OpenAI
`text-embedding-3-small` **1536d**, cosine, L2 normalization Gemini yang diperkecil.
Gemini document `RETRIEVAL_DOCUMENT`, query `RETRIEVAL_QUERY`; OpenAI memakai
endpoint yang sama untuk dokumen/query, label task internal tidak dikirim ke vendor.
`MODEL_REVISION=1` adalah revisi profil internal, bukan snapshot vendor immutable.
Batch 32, chunk target 512 tokens, overlap 64; enforce tokenizer limits sebelum request.

`text-embedding-3-large` valid, default **3072d**, dapat meminta reduced dimensions.
Dokumentasi OpenAI menunjukkan biaya relatif sekitar 6,5x small; vector float32
3072d sekitar 12 KiB versus 6 KiB 1536d, sebelum metadata/index overhead.
Rekomendasi: small untuk baseline biaya, large kandidat benchmark 200 pertanyaan
Indonesia/Inggris/campuran dan paper. Promosikan bila Recall@10 naik ≥5 percentage
points, p95 retrieval ≤300ms dan biaya masih dalam budget. Jangan menjalankan small
dan large pada setiap query tanpa hasil evaluasi. Dua model OpenAI bukan redundansi
dua vendor. Large perlu profil/generation/collection baru, reindex lengkap, evaluasi,
atomic activation dan rollback ke generation lama; dimensi sama pun bukan ruang sama.

## DEC-10/11/14 — runtime dan vendor

- Langflow: **Workflow API v2**, bukan klaim versi aplikasi Langflow 2.x.
  Dokumentasi yang ditinjau versi 1.12.x. Pin exact deployment image digest setelah
  `/api/v1/version` dan OpenAPI server diverifikasi. Server harus mengaktifkan
  `LANGFLOW_DEVELOPER_API_ENABLED=true`. Backend POST `/api/v2/workflows`, body
  `flow_id`, `input_value`, `session_id`, `mode=sync`; hasil `output.text` hanya
  diterima saat `status=completed`, `has_errors=false`. Adapter background sekarang
  mendukung kontrak ini. Streaming/background recovery/cancel perlu spike tersendiri.
  Project MCP masih `/api/v1/mcp/project/.../streamable`; jangan ubah seluruh v1 ke v2.
- Custom components: `custom_langflow_components/teman_bule_runtime.py` dan artefak
  di `runtime-components.md`; build/import/export pada exact image, tanpa secret
  di canvas atau trace. Jangan menyamakan keberhasilan HTTP dengan persist tool sukses.
- CallCraft: pakai `custom_callcraft_spec/integration-manifest.v1.json` dan kontrak
  `.blueprint/callcraft-tools.md`, endpoint `/v1/call`, context trusted service-bound.
  Auth mengikuti adapter dan fixture vendor aktual, bukan nama header baru yang ditebak.
  401/403 → auth denied; 409 → conflict; 422 → invalid args; 429 → rate limited;
  timeout → outcome unknown/reconcile untuk mutasi; 5xx → dependency unavailable.
  JSON spec dan authentikasi masih perlu live contract evidence.
- ElevenLabs: `eleven_v3` valid secara dokumentasi, maksimal 5.000 karakter/request;
  segment podcast di bawah batas itu. Validitas dua voice ID perlu account lookup.
  Model expressive tidak otomatis cocok dengan websocket/plugin realtime.
  `eleven_v3_conversational` adalah model berbeda; perubahan untuk call hanya setelah
  latency/plugin spike dan keputusan eksplisit, tidak sebagai fallback tersembunyi.
- LiveKit: versi backend berasal dari `requirements.lock` yang sudah di-resolve;
  jangan menambah plugin floating. Matrix wajib mencakup Agents, RTC, API, plugin
  ElevenLabs, STT, VAD, SDK mobile native. Nilai concurrency env 50 adalah ceiling
  aplikasi, bukan kuota vendor terverifikasi. Admission aktual min(50, kapasitas load
  test, kuota provider); uji 5 → 10 → 25 → 50 concurrent sessions.

## DEC-13 — persona dan rubric

Persona artifact `elean.v1`: tutor AI hangat, sabar, encouraging; kalimat pendek,
satu pertanyaan per giliran, koreksi 1–2 kesalahan paling penting setelah pengguna
selesai. Deskripsi: “Teman latihan yang membantu kamu berani berbicara.”
`willy.v1`: tutor AI energik, praktis, humor ringan, role-play perjalanan/kerja;
tantangan bertahap dan koreksi langsung yang sopan. Deskripsi: “Partner latihan
untuk percakapan sehari-hari dan dunia kerja.” Keduanya tidak mengarang biografi manusia.
Bahasa default English sesuai level; penjelasan Indonesia saat diminta/kesulitan;
mode ID/EN dapat dipilih. Voice binding terpisah dan immutable per versi sesi.
Avatar arah desain: ilustrasi original bergaya flat, Elean teal, Willy amber,
kontras tinggi; aset final berlisensi dan hash perlu dipublish, bukan URL palsu.

Rubric `toefl-practice.v1`: simulasi latihan **bukan skor resmi ETS**. Objective
reading/listening = correct/valid items ×100, tanpa penalti jawaban salah.
Writing: task fulfillment 30%, organization/coherence 25%, grammar 25%, vocabulary 20%.
Speaking: task fulfillment 30%, delivery/intelligibility 30%, grammar 20%, vocabulary 20%.
Setiap dimensi integer 0–4: 0 kosong/off-topic; 1 sangat terbatas; 2 sebagian tercapai
dengan gangguan bermakna; 3 jelas dengan kesalahan minor; 4 lengkap/jelas/konsisten.
Practice score = round-half-up(sum(weight × dimension/4)), bounds 0–100.
Tidak mengonversi otomatis ke TOEFL 0–120 atau band resmi tanpa kalibrasi.
Speaking tanpa audio usable = unscorable, bukan skor nol; delivery butuh evidence audio.
Feedback JSON: rubric_version, response_id, scorable, dimensions[{code,score,max:4,
weight,evidence}], practice_score, strengths[1..3], improvements[1..3], corrected_example,
next_exercise, feedback_locale (id/ en). Backend menghitung aggregate; evidence harus
merujuk teks/timestamp nyata. Publish test version dan rubric sebelum flow aktif.

## DEC-15 — podcast/media

Target 300s; hard max 600s elapsed playback termasuk interupsi; extension pool 60s
total, maksimum 3 interupsi, closing grace 15s, idle timeout 120s. Extension tidak
melampaui hard deadline; reconnect tidak reset waktu. Barge-in cancel generation,
flush audio, checkpoint, jawab singkat grounded paper, lanjut segmen tersimpan.
PDF maksimum 10 MiB, 30 halaman, expanded text 2 MiB, parse 30s. Scan malware sebelum
parse. OCR **off MVP**; scan/image-only PDF ditolak dengan instruksi upload searchable
PDF. Password-protected/invalid/oversize gagal eksplisit. Citation minimal page/source.

## DEC-16 — data dan operasi

Retention baseline: conversation 180 hari, media 7, podcast source 7, generated audio
30, audit 90, webhook raw teredaksi 14, expired auth session 30 setelah expiry/revocation.
Financial 3653 hari sebagai buffer 10 tahun; detail kebutuhan akuntansi/pajak perlu
review yurisdiksi. Retain financial minimal metadata, bukan isi percakapan.
Consent version tercatat saat onboarding; izin microphone/camera just-in-time;
memory/personalization dan analytics opsional terpisah dan dapat dicabut. Raw video
tidak direkam. Deletion tombstone segera, purge primary/vector/object ≤30 hari;
backup lifecycle 35 hari, replay tombstone sesudah restore; legal hold minimal eksplisit.
Tugas retention perlu scheduler dan verification; mengisi env tidak menjalankan purge.

SLO target launch: API core 99,9%/bulan (sekitar 43,2 menit error budget/30 hari),
AI job success ≥99% selain invalid user input; dependency failures tetap diukur.
p95 core API ≤500ms, text first token ≤3s, voice turn first audio ≤2,5s.
PostgreSQL RPO ≤15 menit, RTO ≤4 jam; daily backup + continuous WAL/PITR, restore drill
bulanan. Ledger yang hilang akibat disaster harus direkonsiliasi sebelum billing dibuka.
Vector/cache dapat rebuild dengan RTO 24 jam; canonical SQL/object tetap sumber kebenaran.
Alert burn-rate dan cost harian; review margin/Advance subsidy setiap minggu.
Target ini belum bukti gate P10 lulus.

## FE-01–FE-09 — kontrak frontend

| ID | Keputusan baseline | Dependensi nyata |
|---|---|---|
| FE-01 | EAS development (dev-client), preview (internal), production (store); pisah backend URL; signing via EAS credentials; physical-device build untuk call | Store IDs, bundle/package namespace, Apple team, certificates, Expo project ID harus berasal dari akun owner; repo ini backend |
| FE-02 | Google system browser ASWebAuthenticationSession/Custom Tabs, authorization-code + PKCE/state/nonce; handoff single-use code, bukan bearer token di URL; Universal/App Links | Callback/domain association dan endpoint code exchange mobile belum dibuktikan tersedia |
| FE-03 | Cursor pagination default 20 max 100; messages default 50 max 100; stable created_at+id, opaque cursor; response items,next_cursor,has_more | GET courses, course structure, podcasts, TOEFL tests harus tersedia/terdokumentasi; route structure usulan `/v1/courses/{id}/structure` belum kontrak implemented |
| FE-04 | SSE backend → client, POST untuk send/cancel; bearer header; Last-Event-ID replay 10 menit, heartbeat 15s, reconnect jitter 1/2/4/8s max 30s; resume state setelah replay expired | Jangan replay POST generation saat stream reconnect; native client harus mendukung authenticated streaming |
| FE-05 | Join DTO target `{session_id,server_url,room_name,participant_identity,access_token,expires_at}`; fetch ulang token melalui backend setelah TTL 300s; reconnect grace 30s | Cocokkan OpenAPI DTO aktual sebelum frontend; mic background hanya explicit active call + OS indicator, camera off saat background, putus jika permission/audio focus hilang |
| FE-06 | Limit PDF DEC-15; UX upload→scan→parse→index→script→audio→ready, failed/cancelled/retry; progress berbasis stage, bukan persen buatan | Resume dengan job ID; retry idempotent dari stage checkpoint |
| FE-07 | Hosted checkout system browser, HTTPS return/App Link menuju order status; poll 2/4/8s max 30s lalu pending; saldo dari server | MVP transaksi web; store mobile digital token memakai IAP/Play Billing bila diwajibkan storefront. External Xendit link di native tidak diaktifkan sebelum entitlement/policy storefront dipastikan |
| FE-08 | Privacy ID/EN sebelum register; OTEL collector untuk operasi, analytics opt-in tanpa chat/audio/keys, tanpa session replay; WCAG 2.2 AA, tap target ≥44pt iOS/48dp Android, font scaling 200%, reduced motion, transcript | Public policy URL, controller/contact nyata dan DPA vendor harus diisi owner; accessibility diuji di perangkat |
| FE-09 | Aset persona original; copy ID default + EN; curriculum MVP A1/A2/B1 masing-masing 4 unit ×3 lessons (36), 5–10 menit/lesson; vocabulary+role-play+quiz | Author/review/publish konten berlisensi; tidak mengisi lesson kosong sebagai published |

## Bukti dan langkah aktivasi

Dokumentasi dibaca 2026-09-28:
- https://developers.openai.com/api/docs/guides/embeddings
- https://docs.langflow.org/workflow-api-quickstart
- https://docs.langflow.org/api-reference-api-examples
- https://elevenlabs.io/docs/models
- https://docs.xendit.co/docs/payment-sessions-overview

Dokumentasi bukan hasil live account test. Akses docs Gemini gagal pada sesi ini;
metadata/dimensi akun masih perlu probe. Xendit v3 exact schema masih belum diperoleh.
Urutan aktivasi: metadata probes → contract fixtures → reviewed registry publication
packages/rates/personas/rubric → sandbox flows → load/restore drills → enable feature.
Rollback konfigurasi Langflow ke `/api/v1/run` harus eksplisit bersama flow yang
kompatibel; perubahan model/profile/rate selalu revision baru, tanpa edit history.
