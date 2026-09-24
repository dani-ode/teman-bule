# Auth dan Provider Policy

## Email/Password

Backend mengelola identitas aplikasi. Email disimpan canonical-normalized dengan unique index; password di-hash Argon2id, tidak dienkripsi reversibel. Registration membuat account unverified, mengirim email verifikasi melalui SMTP durable job. Token verifikasi/reset adalah random cryptographic, hash-only di DB, purpose-bound, single-use dan expiring. Response register/reset tidak membocorkan keberadaan email. Resend/rate limit berbasis IP + identity; password policy dan Argon2 cost tervalidasi config.

Login memerlukan email verified dan account aktif. Access JWT pendek memakai asymmetric signing, issuer/audience/kid, expiry. Refresh opaque token hash-only, rotation per penggunaan, family reuse detection mencabut family. Logout mencabut session; logout-all/password reset mencabut seluruh refresh session dan menaikkan user auth epoch agar access token lama ditolak. Web memakai refresh cookie HttpOnly/Secure/SameSite dan validasi Origin/CSRF pada mutasi berbasis cookie; access token di memory.

Access JWT membawa stable session-family reference; setiap authenticated request memeriksa family aktif dan auth epoch melalui authoritative session lookup/cache dengan revocation semantics yang ditetapkan DEC-02. Logout/reuse detection menolak access token family terkait, sementara logout-all/reset menolak seluruh epoch lama. Referensi family tetap stabil saat refresh token rotation.

## Google OAuth / OIDC Login

Backend authorization-code flow dengan state, nonce, PKCE S256, redirect URI exact allowlist dan callback `/v1/auth/google/callback`. State/nonce/verifier terikat pada browser transaction, expiring dan single-use. Tukar code server-side; verifikasi ID token signature/JWKS, issuer Google, audience client, expiry, nonce, `sub`, dan `email_verified`. Provider identity unique `(provider, subject)` menjadi kunci login; email bukan identity key federasi.

Google user baru mendapatkan account verified. Jika email sudah milik account lokal, **jangan auto-link hanya karena email sama**: user login account existing dan melakukan explicit link dengan reauthentication + transaksi OAuth baru. Unlink tidak boleh menghapus satu-satunya metode login. Google tokens tidak menjadi app access token; token provider tidak dipersist tanpa scope tambahan yang disetujui. Callback menetapkan session dan redirect hanya ke URL frontend allowlisted; tidak menaruh token dalam URL.

## Provider/Model Katalog

- `provider_catalog` aktif hanya Gemini dan OpenAI pada seed awal. Provider key terpisah dari capability model: jangan mengasumsikan semua model Gemini bisa STT streaming atau vision.
- Model memiliki protocol adapter/version, provider model identifier, capability flags (`llm_text`, `stt_batch`, `stt_stream`, `vision_input`, `tool_calling`, `embedding`), context/output limits, supported input formats, metering contract dan deployment availability.
- LLM dan STT user dipilih independen. Call memerlukan `stt_stream` atau adapter chunked yang lulus latency budget; voice note cukup `stt_batch`. Video memerlukan `vision_input`. Unsupported capability → 422 sebelum room/job/billing dimulai.
- User tidak bisa menambahkan provider/model identifier atau mengubah capability. Katalog disabled memblokir invocation baru. Model embedding admin tidak menjadi pilihan BYOK user.

## BYOK Advance dan Base URL

Credential record terenkripsi dengan envelope encryption/KMS, key version, fingerprint, provider dan base URL policy. Payload create adalah provider ID, API key, optional base URL. Selection mengacu ke model ID + credential ID; validasi memastikan owner dan provider cocok. Satu credential boleh melayani LLM/STT jika capability lolos; pengguna dapat menyimpan beberapa credential per provider.

Base URL kosong berarti canonical endpoint katalog. Custom URL wajib HTTPS dengan host/port/path policy provider, tanpa embedded credential, query secret atau fragment; validasi DNS/alamat tujuan setiap koneksi, block loopback/private/link-local/metadata, IPv4/IPv6, redirects dan DNS rebinding. Proxy custom perlu host allowlist admin dan protocol compatibility verification. User tetap bisa memasukkan URL, tetapi tidak dapat membypass allowlist atau menciptakan model/provider baru.

`verify` melakukan probe minimum dengan consent bahwa provider mungkin mengenakan biaya, bounded quota, dan tanpa fallback. Status `pending_verification → active|invalid`, `active → revoked|invalid`; timeout transient tidak dianggap invalid permanen. Response hanya fingerprint/status/provider/base URL tersanitasi; key tidak pernah dikembalikan. Delete/revoke menghapus key material dan membatalkan future invocations serta queued jobs terkait.

## Credential Ownership

VIP LLM/STT memakai `VIP_*` server settings. ElevenLabs memakai `TTS_API_KEY`; dua voice binding dari agent versions. Seluruh embedding Gemini/OpenAI memakai `GEMINI_API_KEY` / `OPENAI_API_KEY` admin, termasuk query vectors untuk Advance. Background maintenance memakai `BACKGROUND_AI_*` dengan admin credential terpilih. Kegagalan key tidak pernah memicu pengalihan diam-diam antara VIP, Advance, atau admin.
