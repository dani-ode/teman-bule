# Contract Artifacts — Draft v1

Artefak ini menghubungkan blueprint dengan persiapan CallCraft, Langflow dan MCP. Status **draft**, bukan hasil deployment atau bukti kompatibilitas vendor. Perubahan kontrak wajib diperbarui bersama blueprint sumbernya.

## Indeks dan traceability

| Artefak dari root repository | Sumber / kegunaan |
|---|---|
| `custom_callcraft_spec/common.schema.json` | `callcraft-tools.md`: request/response, error, ULID, optimistic version |
| `custom_callcraft_spec/tools.v1.json` | 11 baris Required V1 Tools, endpoint, scope, argumen/hasil, purpose, idempotency dan audit |
| `custom_callcraft_spec/callcraft-template.v1.json` | Template format contoh export pengguna; satu hasil render per tool setelah spike vendor |
| `custom_callcraft_spec/integration-manifest.v1.json` | Aturan rendering, registry, MCP routing dan gate vendor |
| `custom_langflow_components/flows.v1.json` | 16 workflow `langflow-flows.md`, cost owner, input/output semantik dan allowlist |
| `custom_langflow_components/callcraft-component.v1.json` | Interface target dan gap terhadap contoh Python yang tersedia |
| `custom_langflow_components/runtime-broker.v1.json` | Kontrak gateway dan kebijakan credential untuk adapter Python baru; lihat `runtime-components.md` |

Seluruh tool JSON disimpan di `custom_callcraft_spec/` mengikuti `.agents/rules/callcraft.md`. Dokumentasi arsitektur berada di `.blueprint/`.

## Cara memakai kontrak

1. Pilih tool berdasarkan nama dan versi di katalog. Validasi envelope menggunakan `common.schema.json`, kemudian `arguments` memakai `inputSchema` tool. Untuk mutasi, `idempotency_key` wajib walaupun tidak wajib pada envelope bersama.
2. Semua relative `$ref` schema tool di-resolve dari folder `custom_callcraft_spec/`. Pada hasil sukses, validasi `result` memakai `outputSchema`; hasil gagal wajib `result: null` dan structured error. Jangan menganggap metadata katalog sebagai JSON Schema root.
3. Terapkan invariants domain, owner scope, source bounds, rubric, allowlist dan configured limits di backend. Schema saja tidak membuktikan otorisasi atau transisi state.
4. Setelah gate vendor terverifikasi, render satu spec per tool dari template. Semua `${...}` harus terselesaikan; template mentah tidak boleh diimpor. `id`/`projectId` adalah data deployment dan `callcraft_spec_id` masuk `tool_registry`.
5. MCP memakai schema argumen yang sama, tetapi trusted context diinjeksi runtime. Routing tetap melalui CallCraft. Verifikasi alias nama dan kompatibilitas schema terhadap SDK deployment.

## Keputusan yang diselaraskan

- `learning.record_progress` tersedia sebagai kontrak tetapi allowlist kosong: blueprint hanya mengizinkan progress read dari learning assistance. Aktivasi write membutuhkan perubahan policy eksplisit.
- `session_context_preparation` default tanpa tool; subset read-only perlu keputusan eksplisit karena blueprint belum menentukan nama tool.
- `direct_realtime_call` dan `podcast_runtime` adalah label purpose runtime kontrak, bukan workflow Langflow baru.
- Lifecycle vocabulary mengikuti `product-requirements.md`. DTO tambahan (misalnya `profile_version`, struktur extraction minimal, `provenance_ref`) adalah rancangan v1 yang harus dibuktikan saat backend contract slice.
- Catalog flow saat ini kontrak semantik; per-flow input/output JSON Schema rinci, canvas export dan fixtures aktual menjadi gate implementasi berikutnya, bukan diklaim sudah tersedia.

## Gate sebelum staged/active

- Phase 0: buktikan vendor binding ke endpoint backend, service auth, trusted context, error mapping, idempotency, MCP, import/export, cancellation dan secret non-persistence. Contoh export ekstraksi dokumen belum membuktikan backend binding.
- Putuskan batas payload/list/context, timeout, progress status/first-write semantics, level/preference allowlist, fact policy dan rubric bounds. Field terbuka pada draft wajib dipersempit sebelum aktivasi; jangan memakai angka atau enum tebakan.
- Lengkapi fixtures request/response per tool: success, invalid arguments, forbidden owner, stale version, idempotency replay/conflict dan dependency failure sesuai jenis tool. Fixtures vendor harus berasal dari spike teredaksi.
- Phase 3: vertical slice `vocabulary.save` setelah foundation dan billing sesuai implementation plan; pastikan hasil tersimpan, audit tercatat, retry tidak menggandakan mutasi/debit dan tutor tidak mengklaim sukses palsu.
- Setelah slice terverifikasi, ekspansi ke seluruh tool/flow; immutable artifact version/checksum, staged activation dan rollback registry.

## Aturan perubahan

Pemeriksaan lokal: `.venv/bin/python custom_callcraft_spec/validate_contracts.py`. Memeriksa sintaks JSON, referensi lokal, kesamaan daftar tool/flow terhadap tabel blueprint, endpoint/scope/idempotency dan allowlist dua arah. Pemeriksaan ini bukan validator semantik JSON Schema atau uji import/runtime vendor.

Perubahan nama, endpoint, field wajib, scope, purpose, biaya atau state harus memperbarui katalog dan dokumen sumber di PR yang sama. Breaking schema menghasilkan versi baru. Deployment tidak boleh mengaktifkan artifact draft, placeholder yang belum di-render, atau kontrak dengan activation gate belum selesai.
