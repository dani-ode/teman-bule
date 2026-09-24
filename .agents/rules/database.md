---
trigger: model_decision
description: Database operations, migrations, and data integrity
---

- **Schema First:** Database schema/migration must be created before any handler or persistence code. Follow `.blueprint/postgresql-schema.md` table definitions exactly.
- **Migration Policy:** Alembic forward-only for shared environments. Compensating rollback must be reviewed. Fresh database creation and upgrade from previous schema must both be tested.
- **Transaction Boundary:** Use UnitOfWork pattern. Domain mutation, required audit events, and outbox events must be in the same SQL transaction. No implicit commits in repositories.
- **No Cross-Module Direct Table Access:** Modules access other modules' data only through published application interfaces, never by directly querying their ORM models or tables.
- **Constraint Enforcement:** Use SQL CHECK constraints, FK constraints, unique constraints, and composite indexes as defined in the schema. Application-level validation complements but never replaces database constraints.
- **Append-Only Tables:** Ledger entries, audit events, and usage records are append-only. Database triggers must prevent UPDATE/DELETE on these tables.
- **ULID Primary Keys:** All entity primary keys use ULID `varchar(26)` validated format. No sequential IDs or UUIDs for domain entities.
