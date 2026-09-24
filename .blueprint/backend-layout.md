# Backend Layout dan Dependency Boundaries

Status: rancangan layout untuk Phase 1; packaging/install mechanics ditutup melalui DEC-01 sebelum FND-01. Entry points mengikuti `toolchain.md`. Direktori dibuat ketika implementasinya diperlukan, bukan sebagai placeholder yang mengklaim feature ready.

```text
src/temanbule/
  api/                 # main:app, HTTP/SSE transport, middleware, composition root
  worker/              # durable worker entry point, dispatch/composition
  realtime/            # LiveKit entry point dan media lifecycle (Phase 6)
  platform/            # settings, SQL unit-of-work, messaging, observability, crypto adapters
  modules/
    identity/
    catalog/
    billing/
    ai_runtime/
    conversations/
    vocabulary/
    learning/
    assessments/
    knowledge/
    media/
    podcasts/
migrations/            # Alembic revisions; schema sesuai slice
tests/
  unit/
  integration/
  contract/
  e2e/
```

Di dalam modul, pisahkan `domain/` (invariants/state/value objects), `application/` (use cases/ports/transaction orchestration), `infrastructure/` (repositories dan vendor adapters), serta `contracts/` (DTO/event schema publik modul) bila diperlukan. Hindari folder util umum yang menjadi tempat business logic lintas modul.

## Aturan dependensi

1. Domain tidak mengimpor FastAPI, SQLAlchemy, Redis atau SDK vendor. Application memakai domain dan typed ports. Infrastructure mengimplementasikan ports; composition root memasang implementasinya.
2. Handler HTTP, consumer job dan realtime adapter memakai application use cases. Mereka tidak menyalin aturan billing/ownership atau langsung memutasi tabel modul lain.
3. Antarmodul memakai application interface/kontrak published, bukan repository/model ORM internal. Circular dependencies harus diselesaikan melalui orchestration/ports/events.
4. `billing` satu-satunya pemilik ledger/reservation/settlement. `identity` pemilik principal/session. `ai_runtime` mengoordinasikan snapshot, grant dan invocation melalui interface pemilik data.
5. Application use case menetapkan unit-of-work. Domain mutation, audit yang diwajibkan dan outbox terkait berada dalam SQL transaction yang sama. Tidak ada implicit commit di repository.
6. Vendor/network invocation tidak menahan transaksi SQL lintas network wait. Persist intent/authorization terlebih dahulu, panggil vendor, kemudian persist result/checkpoint; recovery menggunakan invocation identity dan reconciliation.
7. Redis mengantarkan pekerjaan, SQL menentukan state/lease/dedupe. Worker ack setelah durable result; retry tidak mengulang billable invocation tanpa kepastian/reconciliation.
8. SDK vendor hanya di adapter. Model/provider/config berasal dari settings/catalog yang tervalidasi, tanpa fallback otomatis.
9. Langflow custom components dan CallCraft JSON tetap berada di direktori yang diwajibkan rules. Backend menyediakan trusted APIs/ports; tidak memindahkan AI processing ke handler demi melewati integrasi.
10. Import boundaries diperiksa pada review dan automated architecture checks ketika source tersedia. Strict types dan validasi DTO tidak menggantikan ownership checks/invariants domain.

## Kontrak sebelum implementasi

Untuk setiap endpoint/event: tentukan fields/types, required/nullability, limits bersumber policy, owner/scope, stable error codes, idempotency/concurrency dan schema version. Pydantic/OpenAPI dihasilkan dari kontrak aplikasi, diuji terhadap fixtures dan ditinjau sebelum handler aktif. Streaming harus menetapkan sequence/replay/terminal semantics sesuai `api-events.md`.

Migration menggunakan constraint SQL, FK/index dan privilege yang diperlukan invariants. Shared environment forward-only dengan compensating migration yang direview; upgrade dari schema sebelumnya dan fresh database harus diuji. Production seed berupa published/versioned catalog policy eksplisit, bukan sample data agar startup tampak sukses.
