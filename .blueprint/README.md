# TemanBule Backend Blueprint

> Versi 3.0.0 — spesifikasi target produksi, tahap desain/persiapan; belum implementasi.
> Bahasa utama: Indonesia. Rules `.agents/rules/` berlaku untuk seluruh fase.

TemanBule adalah backend pembelajaran bahasa Inggris dengan persona **Elean** dan **Willy**, dua mode penggunaan **VIP** (wallet token prabayar melalui Xendit) dan **Advance** (BYOK LLM/STT), serta lima halaman frontend: Home, Chat, Call, Podcast, Profile. Tidak ada subscription bulanan/tahunan pada scope ini.

## Peta Dokumen / Urutan Baca

1. `product-requirements.md`: fitur, batas scope, acceptance produk.
2. `architecture.md`: otoritas komponen dan jalur realtime/background.
3. `billing-plans.md`: token, tarif, reservasi, pembayaran, perpindahan plan.
4. `auth-provider-policy.md`: email/password, Google OAuth, katalog, BYOK.
5. `realtime-podcast.md`: voice/video call dan podcast interaktif.
6. `postgresql-schema.md`: data relasional, constraint, state.
7. `astra-collections.md`: fan-out embedding Gemini/OpenAI dan retrieval.
8. `langflow-flows.md`: banyak workflow, input/output, prompt governance.
9. `callcraft-tools.md`: pusat function calling dan kontrak argument.
10. `api-events.md`: API, event, streaming, failure behavior.
11. `environment.md`: kontrak konfigurasi `.env` / `.env.example`.
12. `technology.md`, `security-operations.md`, `docker-deployment.md`: stack dan operasi.
13. `implementation-plan.md`: urutan implementasi dan release gates.
14. `toolchain.md`: dependency locks, Make commands, CI, Docker/Compose dan entry-point contract.

## Aturan Mengikat

- PostgreSQL adalah sumber kebenaran; Astra adalah projection yang dapat dibangun ulang.
- Langflow pusat AI non-realtime, ingestion, ekstraksi fakta, assessment, dan indexing. LiveKit worker menjalankan STT → LLM → ElevenLabs langsung untuk latency; prompt/persona tetap artifact terversi.
- Semua tool/function calling AI melalui CallCraft. CRUD deterministik, auth, pembayaran, dan ledger tetap domain backend.
- Provider/model dipilih dari database aktif, bukan string bebas dari user. Seed provider aktif hanya `gemini` dan `openai`; kemampuan model harus diverifikasi.
- TTS ElevenLabs dan seluruh embedding ditanggung platform, tidak mendebit wallet VIP dan tidak menggunakan BYOK.
- Secrets tidak masuk blueprint, flow export, queue, logs, traces, atau database plaintext. `.env` lokal mempertahankan credential yang sudah diisi; `.env.example` tidak berisi secret.
- Tidak ada fallback provider/model, harga, mock data, atau konfigurasi terselubung. Konfigurasi wajib yang kosong menggagalkan aktivasi feature terkait secara eksplisit.
- Outbox + durable worker untuk pekerjaan penting; bukan `FastAPI BackgroundTasks`. Semua delivery diasumsikan at-least-once.
- Migration, JSON/OpenAPI schema, flow/tool artifacts, dan integration tests dibuat pada fase coding. Dokumen ini tidak mengklaim artifact tersebut sudah ada.

## Status Keputusan

Keputusan produk yang belum diberikan ditulis sebagai **release gate**, bukan diisi dengan angka arbitrer: harga paket/tarif token, biaya komersial Advance (baseline desain: tanpa debit wallet), model dan dimensi embedding yang valid, model VIP/STT/video, model TTS, batas podcast/call, retensi, SLO, serta kontrak produk Xendit dan deployment CallCraft/Langflow yang nyata. Daftar lengkap ada di `implementation-plan.md`.

Jika kontrak vendor tidak sesuai spesifikasi, dokumentasikan gap/ADR sebelum mengaktifkan capability tersebut. Blueprint harus diperbarui bersama keputusan; jangan menganggap seluruh sistem siap produksi hanya karena spesifikasinya lengkap.
