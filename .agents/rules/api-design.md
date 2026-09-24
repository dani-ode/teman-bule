---
trigger: model_decision
description: API design, error handling, and versioning patterns
---

- **Error Envelope:** All errors must follow `{"error": {"code": "STABLE_CODE", "message": "User-safe message", "request_id": "...", "details": []}}`. Codes are stable strings, never raw exception messages or vendor error passthroughs.
- **Status Code Mapping:** 422 validation/capability, 401 auth, 403 permission, 404 private resource, 409 conflict/state, 402 insufficient balance, 413 payload, 429 rate limit, 502 provider invalid, 503 unavailable.
- **Idempotency:** All POST mutations must require `Idempotency-Key` header. Same key + same payload = replay stored response. Same key + different payload = 409 IDEMPOTENCY_CONFLICT.
- **Pagination:** Cursor-based pagination with explicit maximum page size. No unbounded list endpoints. Include `next_cursor` in list responses.
- **Optimistic Concurrency:** PUT/PATCH mutations must include `expected_version`. Stale version returns 409 STATE_CONFLICT.
- **Versioning:** Breaking changes to request/response schemas require a new schema version. Contract changes must be versioned and documented in the same PR as the code change.
- **Async Operations:** Long-running operations return 202 with job ID and status URL. Never assert "ready" or "result available" on acceptance.
- **Streaming:** SSE events must include sequence, schema_version, and exactly one terminal event (`completed|cancelled|failed`). Reconnect must not restart provider invocations.
