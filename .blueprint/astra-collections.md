# Astra DB: Dual Embedding Projections

## Sumber Kanonis dan Ruang Vektor

Setiap input embedding menjadi satu canonical document/chunk, lalu **selalu fan-out ke Gemini dan OpenAI** memakai credential admin. Teks/hash/source version sama; vector berbeda. Penggunaan dua provider bukan menggandakan fakta di SQL atau menggandakan tagihan user. Ingestion dan query embedding tidak memakai BYOK.

Collection fisik terpisah per scope, provider, model revision dan dimension. Vector dari ruang berbeda tidak boleh dicampur walaupun dimensinya kebetulan sama. Model/dimension dipilih eksplisit dan diverifikasi saat provisioning; jangan menebak dimensi berdasarkan nama provider. Perubahan model/dimensi membuat collection baru dan reindex.

| Scope | Isi | Mandatory filter |
|---|---|---|
| `user_memory` | Ringkasan, fakta terkonfirmasi/diizinkan, koreksi | `owner_user_id`, source aktif, retention |
| `learning_content` | Published lesson chunks | content/version/access/publication |
| `toefl_feedback` | Feedback, bukan score otoritatif | `owner_user_id`, rubric/source version |
| `agent_knowledge` | Pengetahuan Elean/Willy yang diterbitkan admin | `agent_id`, knowledge version, publication |
| `podcast_sources` | Paper chunks privat | `owner_user_id` AND `podcast_id` AND source version |

Lima scope × dua provider = sepuluh collection aktif awal. Nama dari `ASTRA_COLLECTION_<SCOPE>_<GEMINI|OPENAI>`; profiles/model/dimension ada di registry. Private/shared dipisahkan fisik. Knowledge umum bisa diassign ke kedua agent lewat canonical association eksplisit; user paper tidak menjadi agent knowledge publik.

## Document Contract

Required: `_id`, `$vector`, `text`, `canonical_chunk_id`, `owner_user_id` (null hanya shared published), `visibility`, `scope`, `source_id`, `source_version`, `chunk_index`, `content_hash`, `embedding_profile_id`, `embedding_model_revision`, `dimension`, `schema_version`, `projection_generation`, `created_at`, `retention_until`; conditional `agent_id`, `podcast_id`, `page_start/end`, `session_id`.

ID deterministik hash dari canonical chunk ID + source version + embedding profile + projection generation. `$vector` finite numbers, length persis dimension. Metadata identity/filter diisi trusted component dari execution context, bukan dari model output atau PDF.

## Fan-out dan Consistency

1. Langflow ekstraksi/chunk menghasilkan canonical refs; trusted runtime API commit `knowledge_documents`, `knowledge_chunks` dan outbox dalam SQL.
2. Coordinator membuat dua `embedding_projection_jobs`, unique `(chunk_id, source_version, profile_id, generation)`.
3. Dua branch menjalankan embed + upsert idempoten; acknowledgement baru setelah hash/model/dimension/source version tervalidasi.
4. Record `embedding_projections` per branch: `pending|running|ready|retry_scheduled|failed|deleted`. Overall document `ready` hanya bila kedua profile complete. Satu gagal → `partial`, retry hanya branch gagal; jangan mengulang branch sukses tanpa alasan.
5. Reconciliation memeriksa missing/stale vectors, counts/hash dan tombstone. Race update/delete dicegah dengan source-version fencing; stale upsert tidak menjadi hasil visible dan cleanup wajib.

Query mengikuti snapshot embedding profile: Gemini LLM → profile Gemini, OpenAI LLM → profile OpenAI. Ini policy produk, bukan kebutuhan teknis embedding; profile tidak berubah diam-diam. Query menggunakan admin key untuk profile yang dipilih. Jika profile belum siap, kembalikan explicit indexing/dependency status atau gunakan canonical context yang memang menjadi input flow (misalnya script generation), bukan fallback vector provider.

## Retrieval dan Reindex

- Private query selalu exact owner filter; session/agent saja tidak cukup. Podcast butuh owner + podcast + source version.
- Agent knowledge difilter agent version; learning published knowledge dicari terpisah dari private memory. Merge memakai limit terversi; score lintas model tidak dibandingkan mentah.
- Top-k, threshold, maximum context bytes dan allowed scopes adalah registry policy. Semua teks retrieval adalah untrusted context; citation hanya ke resource authorized.
- Reindex membuat generation baru, memverifikasi kedua provider, lalu atomic activation registry. Old generation dipertahankan sampai in-flight snapshot selesai sebelum cleanup.
- Account/source deletion mencakup kedua profile, semua generation lama, cache dan media; verifikasi zero authorized matches. Tombstone tetap mencegah delayed job menghidupkan data.
