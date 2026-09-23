# Product Requirements

## Produk dan Persona

Tutor Inggris untuk pengguna Indonesia, adaptif terhadap level, mendukung penjelasan bilingual dan koreksi yang konstruktif. Nama persona adalah **Elean** dan **Willy**. Keduanya record `agents`, memiliki versi persona, voice ElevenLabs berbeda, dan knowledge di Astra. Karakter detail harus disahkan dalam persona version; jangan mengarang biografi sebagai fakta. Persona tidak boleh mengklaim tool berhasil sebelum ada hasil otoritatif.

## Lima Halaman Frontend → Tanggung Jawab Backend

| Halaman | Kebutuhan backend | Acceptance |
|---|---|---|
| Home | Course → unit → lesson; video, reading, grammar exercise, quiz; progress; bantuan AI pada konten published | Konten/version privat atau unpublished tidak bocor; jawaban AI grounded dengan citation; progres otoritatif di SQL |
| Chat | Langsung daftar/halaman chat, kategori, pilihan agent, teks dan voice note, riwayat, streaming, vocabulary | Transcript voice note menjadi input chat; retry tidak mengulang pesan/tool/debit; ownership dan kategori aktif diperiksa |
| Call | Pilihan start voice call atau video call melalui LiveKit; pilihan agent | Video mengizinkan user memperlihatkan benda, AI merespons konteks frame; model vision wajib; barge-in menghentikan playback lama |
| Podcast | Upload paper PDF, ekstraksi/chunk, generate diskusi, edit judul, library/status, play ke room LiveKit, interupsi pengguna | Satu orchestrator menjalankan dua peran Elean/Willy dengan suara berbeda; grounded pada paper; resume setelah interupsi; durasi dapat bertambah sampai batas eksplisit |
| Profile | Edit akun, progres/assessment, vocabulary, riwayat TOEFL, menu tes TOEFL, plan VIP/Advance, top-up/ledger dan BYOK settings | TOEFL berada pada navigasi Profile; plan tidak menghapus progres; credential write-only; harga dan saldo dapat diaudit |

## Entitlement

- VIP: pengguna membeli **token aplikasi**, bukan subscription dan bukan provider token mentah. LLM, STT, input vision, dan AI generation podcast mendebit sesuai rate card. TTS ElevenLabs tidak didebit.
- Advance: pengguna menyimpan API key + base URL opsional dan memilih model katalog untuk **LLM dan STT** secara independen. TTS dan embedding memakai credential admin. Baseline tidak ada debit wallet untuk BYOK; monetisasi tambahan menunggu keputusan eksplisit.
- Home non-AI, akun, histori, dan saldo tidak membutuhkan saldo positif. Pekerjaan AI memerlukan plan/configuration/capability yang valid.
- Pemilihan plan wajib sebelum penggunaan AI; tidak otomatis memberi trial token. VIP dan Advance adalah mode aktif eksklusif untuk pekerjaan baru; saldo tetap tersimpan saat berganti.

## Chat, Pembelajaran, dan Memori

Kategori awal sebagai seed DB: `daily_conversation`, `grammar`, `pronunciation`, `job_interview`, `travel`, `free_talk`. User memilih agent dan kategori aktif. Respons menyertakan teks, koreksi, citation, dan hasil tool tervalidasi. Voice note harus lolos validasi media sebelum STT.

Setelah interaksi, Langflow terpisah mengekstrak fakta relatif stabil pengguna (tujuan, level, preferensi), evidence koreksi, ringkasan, dan assessment. Fakta bukan konstanta immutable: ada confidence, provenance, versi, konfirmasi/koreksi pengguna, supersession, dan opsi menghapus. Pesan mentah tetap kanonis di SQL; fakta/evaluasi dan dua projection embedding diproses eventual.

Vocabulary lifecycle: `new → learning → review → mastered`; scheduler deterministik. Duplikasi lemma dinormalisasi per user/bahasa. Learn memakai immutable published content version.

## TOEFL di Profile

Simulasi, bukan skor TOEFL resmi. Objective scoring deterministik; writing/speaking dinilai Langflow dengan rubric version, dimensi dan evidence. Hasil final hanya pada `evaluated`, bukan saat job baru diterima. Kegagalan dapat dilihat dan retry tidak menggandakan nilai/debit. Pembangkitan feedback subjektif mengikuti plan; indexing feedback dan assessment longitudinal ditanggung platform.

## Di Luar Scope

Human tutor marketplace, organisasi multi-tenant, sertifikasi TOEFL resmi, arbitrary provider/model buatan user, subscription berulang, UI admin authoring penuh, avatar video AI, serta dua AI participant LiveKit terpisah sebagai persyaratan podcast. Admin provisioning/catalog tetap diperlukan melalui proses terotorisasi.
