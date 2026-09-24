# Teman Bule Runtime Components

## Status dan keputusan

Tersedia dua adapter Python Langflow dan satu async runtime client. Backend runtime gateway **belum diimplementasikan**. Komponen adalah persiapan integrasi yang dapat diuji lokal, bukan capability aktif. Contoh generik `callcraft_component.py` tetap menjadi referensi vendor.

| File | Tanggung jawab |
|---|---|
| `custom_langflow_components/teman_bule_runtime.py` | Typed request/response, konfigurasi eksplisit, HTTPS async client, bounded response, error sanitization |
| `custom_langflow_components/teman_bule_runtime_context_component.py` | Resolusi metadata runtime terotorisasi |
| `custom_langflow_components/teman_bule_callcraft_component.py` | Eksekusi tool melalui runtime gateway dan CallCraft |
| `custom_langflow_components/runtime-broker.v1.json` | Endpoint, credential policy, server obligations dan activation gates |

## Boundary dan wiring

Backend membuat execution grant berumur pendek dan mengirim `execution_ref` (ULID reference, bukan token) bersama request ID. Grant terikat service identity, owner, snapshot, purpose, scopes dan deadline. Penyimpanan, revocation dan replay semantics grant harus diputuskan pada foundation. Ini mapping transport dari execution context blueprint; backend tetap mengirim signed execution context ke domain tool lewat CallCraft setelah vendor spike.

Adapter Python sekarang hanya mendukung context `plan=vip|advance`; ini cukup untuk draft jalur user, belum untuk admin/shared ingestion tanpa plan. Job platform private mempertahankan owner scope namun payer tetap platform. Sebelum mengaktifkan shared ingestion, version-kan RuntimeContext untuk service principal dan plan nullable, selaraskan schema/tests; jangan membuat user VIP sintetis. Referensi schema target ada di `postgresql-schema.md`.

`RuntimeRequest`: `schema_version: "1"`, `request_id`, `execution_ref`. Node context mengembalikan metadata plan/capability/payer/model dan allowlist, tanpa key atau credential reference yang bisa di-redeem canvas. Input tool menggunakan tiga field request tersebut ditambah `tool_name`, `arguments` dan `idempotency_key` untuk mutasi. Backend/adapter membangun input ini; LLM hanya memilih tool dan argumennya. Output context bukan langsung input tool: builder mengambil request ID/reference dan menambahkan argumen; jangan meneruskan plan/payer sebagai klaim otorisasi.

Jalur eksekusi: **Langflow → runtime gateway → CallCraft → domain API**. Gateway menyelesaikan konfigurasi, registry dan autentikasi; seluruh AI-selected domain tools tetap melalui CallCraft. Gateway tidak langsung menyimpan vocabulary sebagai pengganti CallCraft.

Credential Advance berasal dari PostgreSQL terenkripsi; VIP berasal dari `VIP_*` settings backend. Background/embedding/TTS mengikuti credential platform pada `auth-provider-policy.md`. Resolusi key terjadi di trusted adapter/broker, bukan melalui akses database dari Langflow dan bukan field UI komponen. Tidak ada fallback antar payer/provider.

## Konfigurasi deployment Langflow

Semua wajib, tanpa default tersembunyi; inject ke proses Langflow melalui deployment secret/config:

| Environment variable | Nilai yang harus ditentukan deployment |
|---|---|
| `TEMAN_BULE_RUNTIME_URL` | HTTPS origin backend runtime, tanpa path/query/userinfo |
| `TEMAN_BULE_RUNTIME_SERVICE_TOKEN` | Credential layanan Langflow, terpisah dari user dan provider key |
| `TEMAN_BULE_RUNTIME_CONTEXT_PATH` | `/internal/v1/runtime/context:resolve` sesuai kontrak baru |
| `TEMAN_BULE_RUNTIME_TOOL_PATH` | `/internal/v1/runtime/tools:execute` sesuai kontrak baru |
| `TEMAN_BULE_RUNTIME_TIMEOUT_SECONDS` | Timeout HTTP positif sesuai deployment budget |
| `TEMAN_BULE_RUNTIME_MAX_RESPONSE_BYTES` | Batas response positif sesuai tool policy |

URL harus dari deployment tepercaya. Client menolak redirect, tidak mewarisi proxy environment dan memakai TLS verification. Network egress policy membatasi host backend. Timeout HTTP bukan pembatalan durable job: backend harus menegakkan absolute execution deadline dan menyediakan cancellation/reconciliation saat integrasi.

Deploy package folder `custom_langflow_components` pada Python import path server Langflow; paste satu file adapter saja tidak cukup karena shared module diperlukan. Runtime memerlukan `httpx` dan Pydantic v2; LFX berasal dari versi Langflow yang diverifikasi. Jangan memasang LFX versi tebakan ke dependency backend.

## Failure dan retry

Client tidak melakukan automatic retry mutasi. Error transport membawa status outcome mungkin unknown; reconciliation memakai request/idempotency reference, bukan key baru. Domain failure mempertahankan `status: failed`, `result: null`, structured error. Non-200, JSON invalid, field tambahan seperti secret, atau correlation mismatch menghasilkan exception tersanitasi tanpa remote body. Workflow wajib branch pada status dan tidak merangkum gagal menjadi sukses.

## Persiapan berikutnya / acceptance

1. Foundation: implement execution grant repository/lifecycle, service authentication, broker encryption, registry dan compiled JSON Schema validation. Referensi grant saja tidak pernah cukup untuk authorization.
2. Vendor spike: buktikan CallCraft backend binding, delivery credential ephemeral, provider usage, cancellation dan model selection; capture fixtures teredaksi. Tidak menjalankan provider billable dalam pemeriksaan lokal.
3. Langflow spike: import/build kedua komponen dengan dependency path; pastikan canvas, error, status dan trace tidak menyimpan token/key. Uji concurrent user contexts tanpa shared mutable credential state.
4. Backend contract tests: Advance A tidak bisa memakai grant/credential B; VIP tidak memakai BYOK; embedding Advance memakai platform; revoked key, disabled model, expired grant dan tool salah-purpose ditolak; tidak ada fallback.
5. Phase 3 setelah billing: `vocabulary.save` end-to-end, replay identik, payload conflict, timeout setelah commit dan truthful reporting. Simpan real flow export dan immutable version/hash setelah lulus.
6. Tambahkan adapter direct LLM/STT yang memakai broker untuk node non-CallCraft; metering dan streaming terverifikasi diperlukan. Komponen CallCraft tidak menyelesaikan seluruh jalur provider workflow.

Jalankan pemeriksaan client lokal dengan `.venv/bin/python -m pytest tests/test_runtime_components.py`. Tests menggunakan HTTP transport stub, bukan bukti server/vendor sudah berfungsi.
