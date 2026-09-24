# Environment Contract v3

`.env` adalah konfigurasi lokal ignored; `.env.example` mendokumentasikan key backend yang didukung tanpa credentials. Parity key diperiksa oleh settings validation pada implementasi, bukan diasumsikan identik dengan file lokal. Konfigurasi proses Langflow terpisah di `runtime-components.md`. Backend settings loader belum tersedia, sementara custom runtime client sudah memvalidasi settings miliknya. Nilai development bukan approval konfigurasi production.

## Sumber Konfigurasi

- Env/secret manager: service endpoints, platform credentials, crypto keys, bootstrap model/voice/collection binding, deployment limits.
- PostgreSQL registries: active provider/model capabilities dan base URL policy, plan/catalog/pricing, flow/tool IDs, persona/prompt versions, vector profiles/collection generations dan runtime policy version.
- User settings: encrypted BYOK, base URL yang lolos policy, LLM/STT selection. User keys **tidak** disimpan ke `.env`.
- Env bootstrap values di-resolve ke immutable registry revisions melalui provisioning terotorisasi, bukan INSERT otomatis setiap startup. Drift model/dimension/voice dengan active registry menggagalkan readiness; jangan override session history.

## Group dan Dependency Matrix

| Feature/process | Required groups dan aturan |
|---|---|
| Semua API | `APP_*`, PostgreSQL/Redis, `AUTH_*`, crypto signing; validated URLs/pool/timeout, explicit private key file + kid; SMTP diperlukan email registration/reset |
| Google auth | `FEATURE_GOOGLE_AUTH_ENABLED`, client ID/secret, discovery URL dan exact callback; auth transaction signing/state store dan frontend redirect allowlist |
| AI | Langflow/CallCraft endpoints/auth/registry, execution signing, internal M2M routes, `BACKGROUND_AI_*`; deployed adapters verified |
| VIP AI | Billing active, VIP LLM/STT key/provider/model with catalog compatibility; base URL kosong secara eksplisit memakai canonical endpoint katalog, bukan provider fallback |
| Advance | Crypto envelope encryption/KMS + active catalog LLM/STT; missing/revoked user key gagal pada invocation, bukan admin fallback |
| Dual embedding | Kedua admin keys, provider base URLs, model/revision/dimension/document-query task types, batch settings, metric/index policy dan pasangan collection untuk setiap scope aktif; sepuluh untuk produk lengkap |
| Media | S3 endpoint/bucket/region/auth, signed URL TTL/max upload, scan service URL/auth dan typed scan contract |
| Voice call | AI + media + selected plan ready, LiveKit, ElevenLabs model/key + voice agent, CALL/REALTIME limits, usage checkpoint policy |
| Video call | Voice call + VIDEO limits + catalog vision capability; no raw recording setting implicitly enabled |
| Podcast | AI + media + realtime + dual embedding, both distinct voices, PODCAST duration/parser limits; both personas published |
| Billing | Xendit product/version/env/auth/merchant/callback, DB packages/rates, expiry and BILLING limits; public key optional if selected hosted checkout product does not need it |
| Learn / TOEFL AI | Published curriculum/rubric + flow terkait + dual indexing scope terkait; subjective operations follow selected plan. Read materi/progress dan objective scoring tidak memanggil Langflow |
| OTEL | Explicit collector endpoint/auth when enabled; sampler range valid and content/key redaction |
| Production | Approved all RETENTION fields, secure cookies/TLS, strict CORS/redirects, external secret management and measured capacity policy |

Feature gates default false for unfinished capabilities. `FEATURE_AI_ENABLED=true` existing local setting remains intent only: activation must fail if required models/registries/keys missing. Production AI requires dual embedding enabled; staging foundation may disable AI entirely. Gates never silently remove an advertised capability; readiness/feature response reports unavailable explicitly. Payment reconciliation and existing-job settlement remain available when new billing admission is disabled.

## Key Semantics dan Validation

- `AUTH_JWT_*` replaces previous external resource-server `OIDC_*`. Google OIDC discovery is only federation login. Separate signing keys for app access tokens and AI execution context. Refresh/action tokens opaque hash-only. Secure cookie false is local-only; production true. SameSite/CSRF and explicit origin allowlist required.
- `GOOGLE_REDIRECT_URI` now `http://localhost:8000/v1/auth/google/callback` locally, aligned with APP port. Register this exact value in Google Console before enabling. Production HTTPS callback uses public domain, not `/internal/` route. Use one local hostname consistently for cookies/state.
- `GEMINI_API_KEY` and `OPENAI_API_KEY` admin keys fund all embeddings including retrieval; `BACKGROUND_AI_PROVIDER` resolves only its matching admin key and model. No separate generic `EMBEDDING_API_KEY` that could select one provider accidentally.
- Old single `ASTRA_COLLECTION_*` bindings replaced by provider-suffixed pairs; this config change does not migrate/delete existing remote collections. Provision + reindex + verified activation required. Collection prefix must match deployment environment.
- `VIP_LLM_MODEL` / `VIP_STT_MODEL` existing local strings preserved but unverified. Model catalog/probe must confirm provider, account availability, protocol, streaming/vision and usage. Do not assume an apparently versioned model name exists.
- `TTS_PROVIDER=elevenlabs`, `TTS_API_KEY` platform secret, `TTS_MODEL` required, both voice IDs bootstrap agent bindings. Neither `STT_*` generic key nor single `TTS_VOICE_ID` remains; STT uses plan resolution, voices agent resolution.
- `MAIL_ENCRYPTION=implicit_tls` matches port 465; `starttls` is a distinct mode for a server explicitly configured for it. Certificate validation required. Existing host/login preserved; server handshake must still be verified.
- Xendit environment sandbox/live must match selected credentials/product. `XENDIT_API_VERSION` required explicit vendor-supported value or documented `unversioned` sentinel when endpoint has no version header; never invent header. Localhost webhook URL is a placeholder, not reachable by Xendit: sandbox requires approved public HTTPS callback/tunnel; production public HTTPS mandatory. No remote callback changed in this task.
- Positive integer bounds; refill percentage strictly between 0 and 100; usage checkpoint shorter than reservation window; lease heartbeat implementation shorter than lease TTL. Call/podcast hard maximum greater than target/closing grace, extension never beyond hard maximum. Upload limits harmonize S3 and parser. Frame TTL/in-flight/bytes/resolution all bounded.
- Blank required field is error, never zero, magic default or silent feature disable. Errors list variable names only. Never dump `.env`, secrets, DSNs, OAuth tokens, credentials or full settings object to logs.
- `LANGFLOW_INTERNAL_RUNTIME_BASE_URL` adalah koordinat gateway dari sisi backend; `TEMAN_BULE_RUNTIME_URL` dikonfigurasi terpisah pada server Langflow untuk origin HTTPS gateway yang sama, bersama paths/service token pada `runtime-components.md`. Nama-nama ini bukan alias otomatis. Workflow API v2 memerlukan flag server Langflow dan migrasi adapter eksplisit pada DEC-10.

## Belum Terisi / Aktivasi

Database access/password, JWT/execution signing keys, KMS, M2M tokens/internal URLs, model TTS, embedding models/revisions/dimensions, background model, deployment duration/capacity/parser limits, scan service, billing policy/product and retensi perlu diisi sebelum feature terkait aktif. Prices, flow IDs, catalog IDs dan persona/rubric artifact IDs sengaja tidak diletakkan sebagai hardcoded `.env` prices: semuanya dipublish ke registries.

Nilai credential existing bukan hasil connectivity verification. Tahap ini tidak menjalankan pembayaran, model invocation, OAuth registration, remote workflow import atau provisioning Astra.
