# LiveKit Call dan Podcast Interaktif

## Admission dan Media

Backend memvalidasi principal, plan snapshot, capability, agent version, room capacity dan reserve VIP sebelum mengeluarkan join token. Token TTL pendek, room/participant identity spesifik, grants minimum. LiveKit callback/webhook diverifikasi sesuai SDK resmi, dedupe dan reconcile terhadap session.

Voice call: `VAD → selected STT → selected LLM streaming → ElevenLabs`. Semua langsung di realtime worker; Langflow bukan per-turn dependency. Video call menambahkan sampled camera frames ke input multimodal LLM. AI menjawab lewat suara; avatar/video AI bukan scope. Worker membatasi interval, resolution, byte size, max in-flight dan freshness TTL. Input frame usang/dropped tidak boleh diklaim sebagai observasi saat ini.

Video hanya diambil saat kamera aktif dengan indicator/consent. Tidak merekam raw call audio/video secara default. Frame ephemeral dibuang setelah inference. Vision usage VIP didebit sesuai meter model; TTS tidak.

## Turn State dan Interupsi

Session: `created → connecting → active → ending → completed`; nonterminal dapat `failed|cancelled`. Turn: `listening → transcribing → thinking → speaking → completed`; `interrupted|failed|cancelled` terminal alternatif. Worker memegang session lease + fencing token agar restart tidak menjalankan dua director dalam room sama.

VAD speech-start/barge-in menaikkan generation epoch, membatalkan LLM/TTS request, flush playback queue, dan mengabaikan late packets. Persist transcript user, output generated vs delivered span, interrupt position, usage aktual. Side-effect tool yang sudah commit tidak di-rollback hanya karena audio terinterupsi.

Proactive prompt hanya ketika room connected, user tidak mute, tidak ada speech/LLM/playback aktif dan silence threshold terpenuhi. Cooldown dan maksimum prompt ditentukan policy. Disconnect memakai reconnect grace. End reason eksplisit: user, idle, budget, max duration, provider failure, atau shutdown.

## Podcast: Source sampai Siap Play

1. Create podcast metadata + private paper upload. Judul dapat diedit dengan version check, tidak perlu regenerate script.
2. Finalize upload berdasarkan checksum/size, MIME sniff PDF, malware scan dan parse limits. PDF encrypted/corrupt/unsupported mendapat typed error. OCR hanya jika capability dan policy sudah diputuskan; jangan mengarang teks scanned PDF.
3. Langflow `podcast_document_ingestion` mengekstrak canonical text, page refs, sections dan chunks. Simpan canonical refs lalu enqueue dual projection; text adalah untrusted data, bukan instruksi tool.
4. Langflow `podcast_script_generation` memakai chunks tervalidasi, Elean/Willy, target duration dan runtime snapshot. Hasil outline + ordered speaker segments + citation + estimasi durasi, bukan satu audio monolitik.
5. Status generation `ready` berarti script valid/tersimpan. Status indexing sumber terpisah: `ready` hanya bila kedua profile complete, `partial` jika baru sebagian. Playback admission memeriksa profile yang dipilih snapshot playback; profile itu wajib siap, profile lain boleh partial dengan status yang terlihat. Script ready tidak menjamin setiap plan/provider langsung dapat memulai playback. Tidak fallback embedding diam-diam.
6. Regenerate membuat version baru dengan quote/reserve baru. Script/segment lama immutable; edit judul tidak mengubah source/script version.

Generation states: `draft → uploaded → processing → generating → ready`; langkah berjalan dapat `failed|cancelled`; deletion `deleting → deleted`. Stage job mencatat failure terpisah dan retry dari canonical checkpoint.

## Playback Interaktif

`POST .../playbacks` membuat playback/session baru. Satu **podcast director** menjalankan dua peran dan satu AI participant cukup. Speaker segment `agent_version_id` mengikat persona; snapshot playback mengunci voice configuration version untuk setiap speaker. Tidak perlu dua LLM independen atau dua participant AI.

TTS per segment dengan buffer terbatas. Cache private key mencakup script version, segment hash, voice/model/settings version. Replay cache tidak membebankan ulang generation LLM; TTS baru/cache tetap tanpa debit wallet.

State playback: `created → connecting → playing ↔ interrupted → resuming → playing → closing → completed`; nonterminal dapat `failed|cancelled`. Interupsi user menghentikan playback, STT menangkap pertanyaan, director memilih speaker, LLM menjawab grounded pada paper + dialog yang terdengar, lalu bridge ke segment yang belum selesai. Speaker tidak overlap. Dialog cabang tidak menimpa reusable base script.

Durasi contoh delapan menit hanyalah target, bukan konstanta. Policy berisi target, max extension, idle timeout, max wall-clock, maximum interupsi dan closing grace. Monotonic elapsed termasuk interupsi; pertanyaan tidak me-reset hard deadline. Menjelang batas, director merangkum dan menutup; budget habis juga menutup dengan reason jelas.

Transcript mencatat Elean/Willy/user, sequence, citation, delivered span, usage dan timestamps. Progress checkpoint menyimpan base segment + offset + branch + epoch, accumulated elapsed dan persisted hard deadline. Monotonic clock hanya berlaku selama satu proses; recovery memakai deadline tersimpan sehingga downtime/restart tidak menambah budget durasi. Reconnect tidak membuat room atau menagih invocation yang sama lagi. Restart memerlukan fencing/lease baru dan recovery checkpoint.

## Background Setelah Interaksi

Persist turn/chunk transcript dan outbox, lalu Langflow menjalankan conversation ingestion, fact extraction, learning assessment dan dual embeddings. Range dedupe mencegah setiap interim STT menjadi assessment terpisah. Podcast paper tidak otomatis menjadi fakta pribadi pengguna atau knowledge publik agent.
