# Plans, Token Ledger, dan Xendit

## Definisi dan Kebijakan Biaya

Token aplikasi adalah unit saldo internal, berbeda dari token input/output provider. Tidak ada billing bulanan/tahunan atau expiry saldo otomatis pada baseline. Paket top-up, mata uang, expiry pembayaran, refund policy, dan rate card adalah data versioned DB; nilai komersial wajib disahkan sebelum aktivasi.

| Pekerjaan | VIP | Advance |
|---|---|---|
| Chat/Learn AI, LLM call/podcast, vision frame, subjective TOEFL | Platform key, debit sesuai usage/rate | BYOK LLM, tanpa debit wallet |
| Voice note/call/interupsi podcast STT | Platform key, debit sesuai metering | BYOK STT, tanpa debit wallet |
| Podcast outline/script dan user-requested regenerate | Platform key, estimasi + reserve sebelum job | BYOK LLM, snapshot credential record ID; redeemable reference baru per attempt |
| ElevenLabs TTS termasuk dua suara podcast | Platform expense, **zero wallet debit** | Platform expense |
| Document parsing non-AI, embedding kedua provider, query embedding | Platform expense, **zero wallet debit** | Platform expense |
| Background fakta/memori/assessment longitudinal, indexing | Platform background model/key, **zero wallet debit** | Platform background model/key |
| Playback audio cache tanpa invokasi AI baru | Tidak ditagih ulang untuk generation sebelumnya | Tidak ada debit |

TTS/embedding tetap diukur pada cost observability dengan `payer=platform`; bukan komponen tersembunyi rate card pengguna. Batas penggunaan adil/kapasitas berlaku pada kedua plan. Keputusan biaya Advance tambahan belum ada; implementasi tidak boleh menciptakan subscription atau debit otomatis.

## Rate Card

Immutable version berisi capability, provider/model ID, meter (`input_tokens`, `cached_input_tokens`, `output_tokens`, `audio_ms`, `image_units` sesuai kontrak model), unit quantity dan integer wallet cost numerator/denominator. Meter harus mutually exclusive agar cached/input atau audio/model usage tidak dihitung ganda. Semua angka nonnegatif, tanpa float uang. TTS/embedding tidak boleh masuk charge component pengguna.

Satuan saldo terkecil disimpan `bigint`; display scale metadata. Per invocation: `charge = ceil(sum(quantity × numerator / denominator))`, dihitung dengan arithmetic exact lalu satu rounding di akhir. Perhitungan delta streaming selalu dari cumulative charge dikurangi settled charge, bukan pembulatan tiap frame. Rate version dikunci sebelum invocation. Quote memuat estimate/max reserve, bukan janji harga final; user melihat rate version dan batas spend.

## Wallet dan Reservasi

- Ledger append-only, double-entry journals seimbang per asset; akun user available/held dan platform clearing/revenue/refund. Materialized balances hanya diubah dalam transaksi ledger yang sama, dapat direkonsiliasi.
- Reserve atomik memindahkan available → held dengan row lock/serializable policy dan check saldo nonnegatif. Dua request bersamaan tidak dapat memakai saldo yang sama.
- Setiap invocation mempunyai usage identity unik. Settle memindahkan held → spent clearing; release sisa held → available. Journal unik per operation/type/revision mencegah debit ganda.
- Chat/job membatasi max output/work sesuai reservation. Call/podcast memakai rolling reservation: reserve sebelum window berikutnya, usage checkpoint kumulatif, top-up hold ketika threshold tercapai. Jangan mengizinkan provider bekerja melewati budget yang sudah diotorisasi.
- Jika saldo habis: emit `balance.low` lalu `balance.exhausted`, stop input/provider stream terkontrol, selesaikan TTS penutup platform sesuai policy, end room. Sisa reserve dikembalikan setelah reconciliation.
- Cancellation/error tetap membayar provider usage aktual yang terverifikasi, tidak mendebit pekerjaan yang belum dilakukan. Timeout dengan usage tidak diketahui menjadi `reconciliation_required`; jangan release hold atau retry provider secara buta.
- Provider tanpa metering akhir yang andal hanya aktif jika ada meter deterministik terdokumentasi, bounded authorization, dan acceptance tests. Setelah deadline rekonsiliasi, platform menanggung usage yang tidak dapat dibuktikan dan melepas saldo yang belum terbukti, dengan audit/alert. Tidak ada debit susulan tanpa authorization.
- Adjustment/refund memakai compensating journal, bukan edit/delete ledger. Wallet tidak menjadi negatif karena chargeback; selisih menjadi debt/dispute terpisah yang memblokir pekerjaan berbayar.

## Xendit Top-up

1. Backend menerima `package_version_id` + `Idempotency-Key`; nominal/currency/token quantity berasal dari DB, bukan client.
2. Simpan order `created` dan merchant reference unik sebelum request Xendit. Adapter memakai produk/API Xendit yang diverifikasi pada Phase 0, idempotency vendor jika didukung, lookup reference sebelum retry pada timeout.
3. Simpan provider payment ID, checkout URL tervalidasi, expiry; state `pending`. Redirect frontend hanya untuk UX, **tidak** memberikan saldo.
4. Webhook memvalidasi callback auth menurut produk Xendit terpilih, raw body/signature jika disyaratkan, environment/business identity, reference, payment status, nominal dan currency. Konfirmasi melalui authenticated provider lookup untuk event sukses yang akan mengkredit saldo.
5. Persist webhook inbox/dedupe terlebih dahulu, acknowledge setelah durable acceptance; worker memproses idempoten. Unknown reference/amount mismatch masuk reconciliation tanpa kredit.
6. Transaksi SQL mengunci order dan wallet, mengubah menjadi `paid`, mencatat unique top-up journal dan outbox. Duplikasi event berbeda untuk payment sama tetap hanya satu kredit.
7. Poll/reconciliation memulihkan webhook hilang. Event out-of-order tidak menurunkan `paid`; late success diperiksa status otoritatif dan ditangani tepat sekali.

Status order: `created → pending → paid|expired|failed|cancelled`; `paid → partially_refunded|refunded|disputed` lewat proses terverifikasi. Refund/reversal token dicatat terpisah; token yang sudah dipakai membutuhkan policy/review eksplisit. Refund workflow mencegah spend saldo yang sedang di-hold untuk refund.

## Plan Switching

`PUT /v1/me/plan` mengubah pilihan untuk pekerjaan baru secara optimistic concurrency. Blok dengan 409 jika ada call/playback aktif, invocation user-plan belum terminal/settled/reconciled, atau user-requested generation/evaluation job nonterminal. Platform-funded ingestion/embedding tidak memblokir pergantian plan dan tidak mengambil alih plan baru. Saldo VIP dan credential Advance tetap ada. Aktivasi Advance memerlukan selection LLM/STT aktif dan credential valid; video juga memerlukan vision. Tidak ada fallback ke key admin bila BYOK gagal.

## Acceptance Finansial

Duplicate webhook, timeout checkout, webhook terlambat, race saldo, restart setelah provider response sebelum commit, refund setelah spend, barge-in dan job cancel harus diuji. Invariant: ledger balanced; available/held nonnegatif; satu paid order satu top-up; satu usage satu cumulative settlement; TTS/embedding tidak muncul sebagai debit user; tarif yang sudah dipakai immutable.
