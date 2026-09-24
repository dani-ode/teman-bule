# Foundation Backlog — Phase 1

Seluruh ticket **planned**, belum verified. Penanggung jawab bernama, kontrak rinci dan evidence dilengkapi sesuai `execution-readiness.md` sebelum Ready/Done. Dependencies menunjukkan urutan logis; readiness diperiksa per ticket.

| Ticket | Tujuan / scope | Dependency dan keputusan | Acceptance / failure evidence minimum |
|---|---|---|---|
| FND-01 | Package/src layout, application composition dan import boundaries; API entry point nyata | DEC-01; `backend-layout.md`, `toolchain.md` | Install/import dari clean environment dan container; domain bebas dependency transport/vendor; endpoint live mencerminkan proses nyata |
| FND-02 | Typed settings per service/capability, error envelope, request/trace IDs, redaction dan readiness | FND-01; DEC-02, bagian relevan DEC-12 | Missing/invalid config wajib gagal eksplisit; feature-required config memblokir feature; dependency failure tidak tampil ready; log/error tidak membocorkan secret |
| FND-03 | SQL unit-of-work, initial identity/reliability migrations, repository owner scope dan audit | FND-01–02; DEC-03; `postgresql-schema.md` | Fresh/upgrade migration pada PostgreSQL; rollback transaksi, unique/composite FK, cross-owner rejection, immutable audit policy |
| FND-04 | Generic idempotency record/hash, concurrency dan replay contract | FND-03; DEC-03, DEC-12 | Key+payload sama tidak menggandakan write; hash berbeda 409; concurrent/crash retry konsisten; principal/operation isolation; expiry tidak menghapus dedupe finansial |
| FND-05 | Atomic outbox, SQL jobs, Redis Streams worker, retry/dead letter/lease/fencing | FND-03–04; DEC-03 | Crash setelah commit/sebelum publish, setelah publish/sebelum ack, duplicate delivery, stale lease dan Redis recovery; SQL effect tetap deduplicated |
| FND-06 | Register, email verification, password forgot/reset, durable SMTP delivery | FND-02–05; DEC-02, DEC-05 | Generic anti-enumeration response; hash-only single-use purpose-bound token; expired/replay/concurrent consume ditolak; delivery failure eksplisit dan restart-safe |
| FND-07 | Login, access JWT, refresh rotation, logout/all, epoch dan cookie/CSRF | FND-06; DEC-02–03 | Dua refresh paralel tidak membuat dua successor valid; reuse revokes family; logout/reset menolak token lama; cookie mutasi menolak CSRF; ownership dan inactive/unverified account diuji |
| FND-08 | Google OIDC login dan explicit identity link/unlink | FND-07; DEC-05 | Signature/issuer/audience/nonce/state/PKCE/browser binding; callback replay; email sama tidak auto-link; reauth dan larangan unlink metode terakhir |
| FND-09 | Execution grant schema/ports dan service-auth boundary untuk gateway berikutnya | FND-02–05, FND-07; DEC-02, DEC-04 | Expired/revoked/wrong-audience/wrong-service grant ditolak; concurrent consumption/replay sesuai policy; stored snapshot/event bebas key; vendor transport tetap menunggu SPK-01/02 |
| FND-10 | Operational foundation, CI required checks, build/migrate/start/health/drain dan backup restore | FND-01–09; DEC-16 bagian foundation | Clean setup terulang; PostgreSQL/Redis integration tests; real healthchecks/worker identity; per-service secret injection; one-shot migration; graceful stop/restart; backup restore dan outbox recovery dengan hasil tercatat |

## Detail batas pengerjaan

- FND-03 membuat schema yang diperlukan foundation; tabel fitur lain ditambahkan lewat migration pada slice pemiliknya. Desain ledger dan domain lain tetap mengikuti blueprint, bukan dibuat setengah berfungsi sebagai stub.
- FND-06 tidak menjanjikan exactly-once SMTP jika provider tidak mendukungnya. Job/token state harus durable dan deduplicated; uncertain delivery/retry dicatat, token valid tidak diterbitkan ulang secara tidak terkendali.
- FND-09 menetapkan internal trust foundation; kompatibilitas nyata CallCraft/Langflow dan runtime gateway diverifikasi pada Phase 3. Tidak menutup gate vendor hanya dengan unit test.
- FND-10 memverifikasi API/durable worker yang telah diimplementasikan. Realtime entry point, admission/drain dan kapasitas realtime diverifikasi setelah Phase 6 tersedia; milestone foundation tidak mengklaimnya lulus.

## Verification commands dan evidence

Setelah source tersedia, jalankan `make lint`, `make typecheck`, `make test`, `make check`, `make audit`, `make compose-check`, dan `make build` sesuai toolchain. Tests dengan marker `integration` memerlukan infrastruktur nyata yang tersedia; missing infrastructure tidak boleh menghasilkan klaim integration pass. Live test tetap explicit opt-in.

FND-10 wajib mendokumentasikan clean setup yang dapat direproduksi, configuration prerequisites, command menjalankan PostgreSQL/Redis test, `make migrate`, `make up`, health verification dan restore drill. Jangan menganggap `make up` otomatis migrate. Required CI checks harus menjalankan lint/types/non-live tests, migration checks dan build saat source sudah tersedia; preparation checks saja tidak menutup M1.

Review evidence berdasarkan acceptance, bukan hanya exit code agregat. Tambahkan test IDs/paths pada traceability ketika tests benar-benar dibuat. Tidak ada estimasi deadline atau kapasitas yang dianggap disepakati melalui backlog ini.
