# CallCraft Tool Specification

CallCraft menjadi pusat seluruh tool/function calling AI dari Langflow dan realtime worker (voice/video/podcast). Setiap tool memetakan internal domain endpoint dengan JSON schema/version, service scope, timeout, idempotency, audit dan structured errors. JSON specs disimpan di `custom_callcraft_spec/` pada fase coding. MCP untuk discovery/routing/control mengikuti deployment contract. Tidak ada generic SQL/HTTP/filter, payment, wallet debit atau credential resolver tool yang dapat dipilih LLM.

## Required V1 Tools

| Tool | Internal call spec | Scope | Idempotency | Result reference |
|---|---|---|---|---|
| `vocabulary.save` | `POST /internal/v1/tools/vocabulary:save` | `vocabulary:write` | Required | vocabulary entry ID |
| `vocabulary.update_status` | `POST /internal/v1/tools/vocabulary:update-status` | `vocabulary:write` | Required | vocabulary entry ID/version |
| `vocabulary.get` | `POST /internal/v1/tools/vocabulary:get` | `vocabulary:read` | Not required | restricted vocabulary DTO |
| `profile.update_preferences` | `POST /internal/v1/tools/profile:update-preferences` | `profile:write` | Required | profile version |
| `learning.get_progress` | `POST /internal/v1/tools/learning:get-progress` | `learning:read` | Not required | progress DTO |
| `learning.record_progress` | `POST /internal/v1/tools/learning:record-progress` | `learning:write` | Required | progress ID/version |
| `conversation.persist_extraction` | `POST /internal/v1/tools/conversation:persist-extraction` | `ingestion:write` | Required | extraction record/reference |
| `toefl.record_evaluation` | `POST /internal/v1/tools/toefl:record-evaluation` | `toefl:evaluate` | Required | score ID and attempt state |
| `user_facts.upsert` | `POST /internal/v1/tools/user-facts:upsert` | `facts:write` | Required | fact revision/provenance |
| `learning.record_assessment` | `POST /internal/v1/tools/learning:record-assessment` | `assessment:write` | Required | assessment ID |
| `podcast.get_source_context` | `POST /internal/v1/tools/podcast:get-source-context` | `podcast:read` | Not required | authorized chunks/citations |

## Common Request Context

CallCraft authenticates using service credentials and forwards a backend-issued, short-lived execution token. The internal API validates issuer, audience, expiry, flow purpose, tool name, user ID, scopes, and request correlation. `actor_user_id` is never accepted as an unconstrained tool argument.

```json
{
  "schema_version": "1",
  "execution_context_token": "opaque-signed-token",
  "request_id": "ulid",
  "idempotency_key": "ulid",
  "arguments": {}
}
```

Common response:

```json
{
  "schema_version": "1",
  "execution_id": "ulid",
  "status": "succeeded|failed",
  "result": {"resource_id": "ulid", "resource_version": 1},
  "error": null
}
```

## Argument Contracts

- `vocabulary.save`: `lemma`, `language`, optional `definition`, `example`, `source_message_id`. Server normalizes lemma and upserts only within authenticated owner.
- `vocabulary.update_status`: `entry_id`, `target_state`, `expected_version`. Server enforces ownership and lifecycle.
- `vocabulary.get`: `entry_id` or exact `lemma` + `language`; no raw filter.
- `profile.update_preferences`: allowlisted `english_level`, `learning_goals`, and tutoring preferences. It cannot update identity, role, credentials, or provider catalog.
- `learning.get_progress`: `lesson_id` or a bounded list of lesson IDs.
- `learning.record_progress`: `content_version_id`, `status`, `completion_percent`, `expected_version`.
- `conversation.persist_extraction`: `source_message_ids`, schema-versioned extraction data. It cannot rewrite raw messages.
- `toefl.record_evaluation`: `attempt_id`, `rubric_version`, bounded dimensions, total score, feedback, evaluator flow version. Attempt must be `evaluating` and owned by execution context user.

- `user_facts.upsert`: `fact_key`, typed `value`, `confidence`, `source_message_ids`, `expected_version`, `proposed_status`; enforce consent/provenance; inferred facts cannot silently overwrite user-confirmed facts.
- `learning.record_assessment`: `session_id`, `source_range`, `rubric_version`, bounded dimensions/evidence and suggested level; no direct profile overwrite.
- `podcast.get_source_context`: `podcast_id`, `source_version_id`, bounded `chunk_ids` or query; owner and resource scope enforced, no arbitrary vector filters.

## Authorization Matrix

- `practice_interaction`: vocabulary tools and profile preference tool only.
- Direct realtime call: explicit vocabulary tools and progress read; bounded execution with cancellation, no unbounded audio blocking.
- Podcast runtime: source-context read and explicit vocabulary save only.
- `conversation_ingestion`: extraction tool only.
- `user_fact_extraction`: facts upsert only.
- `learning_assessment`: assessment record only.
- `learning_assistance`: progress read only; writes occur from explicit API actions unless separately enabled.
- `toefl_evaluation`: TOEFL evaluation tool only.

## Error Contract

Errors use stable codes: `INVALID_ARGUMENT`, `UNAUTHENTICATED`, `FORBIDDEN`, `RESOURCE_NOT_FOUND`, `STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `RATE_LIMITED`, `DEPENDENCY_UNAVAILABLE`, and `INTERNAL`. Langflow must not translate a failed tool into a successful user claim.

## CallCraft Setup Deliverables

For every row above create: one CallCraft spec/tool definition, JSON schemas, service auth configuration, timeout, scope mapping, example redacted request/response, contract test fixture, and audit mapping. Exact CallCraft `spec_id` values are deployment data stored in `tool_registry`, not literals in code or this document.
