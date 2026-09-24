---
trigger: always_on
description: Testing standards and requirements
---

- **Test Categories:** Every feature must include unit, integration, and failure-path tests as defined in `.blueprint/security-operations.md` required tests section.
- **No Mock-Only Vendor Claims:** Test doubles must be clearly labeled and never used as evidence of vendor compatibility. Live/billable tests require explicit opt-in marker `live`.
- **Database Tests:** Integration tests must run against real PostgreSQL via testcontainers or `TEST_DATABASE_URL`; never SQLite for migration/constraint verification.
- **Idempotency/Replay Tests:** All mutation endpoints must have duplicate request, concurrent request, and crash-restart replay tests.
- **Ownership Tests:** Every private resource endpoint must have cross-owner access denial tests (IDOR).
- **Coverage:** New code must not decrease overall test coverage. Critical paths (billing, auth, ledger) require 100% branch coverage on state transitions.
- **Markers:** Use registered pytest markers: `integration`, `contract`, `e2e`, `live`. Missing marker registration fails the test suite.
