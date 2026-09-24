# Security, Operations, And Quality

## Security Baseline

- Validate app JWT issuer/audience/signature/expiry/auth epoch and active session family as defined in `auth-provider-policy.md`. Google login verifies OIDC issuer/audience/signature/nonce/subject; explicit account linking. Argon2id passwords, single-use verification/reset, refresh rotation/family reuse detection, CSRF/Origin checks and auth throttling are mandatory.
- Enforce resource ownership in application service/repository queries, including sessions, scores, media, vectors, and credentials.
- Encrypt BYOK with envelope encryption and key versioning. Plaintext is never logged, returned, queued, placed in audit metadata, or persisted by Langflow.
- Validate optional provider `base_url`: provider capability enabled, HTTPS, allowed port/host, DNS rebinding protection, private/link-local/metadata IP denial, redirect denial, response size/time limits, and egress policy.
- Treat prompts, retrieved documents, transcripts, and tool arguments as untrusted. Tool allowlist and authorization are out-of-prompt controls.
- Rate-limit auth subject, IP, operation, Langflow run, tool execution, media upload, and call creation with explicit limits from configuration.
- Keep public and internal routes separate. Database, Redis, internal tools, Langflow service credentials, and telemetry endpoints are not publicly exposed.

## Data Classes And Default Retention

| Class | Examples | Rule |
|---|---|---|
| Secret | API key, service token, encryption key | Encrypted/referenced, never logged; removed immediately on revocation/deletion |
| Account | Profile, identity subject | Retain while account active; delete/anonymize by policy |
| Learning | Progress, vocabulary, TOEFL score | User-accessible; delete/anonymize on account deletion |
| Conversation | Chat, transcript, summaries | Configurable retention; private by default |
| Media | Voice note/audio | Shortest practical configured retention; private signed access |
| Audit | Security/tool events | Redacted, append-only, restricted; legal retention configured |

Production deployment must replace policy placeholders with approved durations before accepting real users. User deletion covers PostgreSQL, Astra, object storage, Langflow persisted artifacts if any, and credential ciphertext, with verifiable job completion.

## Observability

- Structured logs: request/trace ID, actor pseudonymous reference, route/use case, flow/tool version, latency, outcome, safe error code. Do not log message/audio content by default.
- Distributed traces: API, SQL, Redis, worker, Langflow, CallCraft, Astra administrative calls, STT/TTS, and LiveKit worker.
- Metrics: request and stream success/latency, time-to-first-token/audio, STT/LLM/TTS latency, tool outcomes, queue depth/oldest age/retries/dead letters, invalid flow contracts, retrieval empty rate, token/cost aggregate, active calls, interruption rate.
- Audit: credential lifecycle/use, AI selection, tool writes, TOEFL scoring, admin access, account deletion, registry activation.

Numeric SLOs and alerts must be approved before production. At minimum define API availability, chat first token, call first audio, ingestion completion window, tool/Langflow availability, queue age, and PostgreSQL RPO/RTO.

## Retry Policy

- Retry only classified transient failures with exponential backoff, jitter, attempt cap, and total deadline.
- Reuse the same idempotency key for write retry.
- Do not retry validation, auth, ownership, schema contract, or state conflict failures.
- Use circuit breakers/bulkheads around Langflow, CallCraft, STT/TTS, and Astra to protect API/worker capacity.
- Terminal jobs enter `failed`, emit metric/alert, and require explicit replay after cause resolution.

## Required Tests

1. Unit: domain states, normalization, ownership policy, credential redaction/encryption boundary, idempotency, event creation.
2. Integration: PostgreSQL migrations/repositories/outbox/ledger locking, Redis recovery, Google OAuth and app JWT validation, refresh reuse, media signing.
3. Contract: backend-Langflow schemas/stream, Langflow-CallCraft tools, CallCraft-internal API, event versions.
4. AI evaluation: tutor quality, bilingual correction, grounding, tool choice and truthful results, malicious context, empty retrieval.
5. End-to-end: VIP/Advance LLM/STT, Xendit top-up, profile, practice, vocabulary, dual ingestion, Home, Profile TOEFL, voice note, video call and Elean/Willy podcast interruption/deadline/reconnect.
6. Security: IDOR, prompt injection/tool escalation, SSRF, secret leakage, expired token, replay, rate limit, oversized media/output.
7. Resilience/load: duplicate events, restart after commit, dependency timeout, backlog, reconnect, concurrent updates, call soak.

## Minimum Runbooks

- Langflow unavailable/invalid output; CallCraft failure; Redis backlog/dead letter; Astra outage/filter anomaly; PostgreSQL saturation/restore; BYOK decrypt failure; STT/TTS/LiveKit outage; account deletion stuck; vector rebuild/reconciliation; exposed secret rotation.

## Financial, Media, dan Projection Operations

- Daily ledger reconciliation: balanced journals, wallet cache vs ledger, active/expired holds, usage unknown deadline, Xendit paid vs credited/refunded. Alert mismatch immediately; repair via compensating journal only.
- Verify webhook identity/environment/reference/amount; durable inbox dedupe and retry, never credit from browser redirect. Financial dedupe retention must outlive generic idempotency TTL.
- PDF scan/parser sandbox resource limits, owner-scoped signed URLs, private cache, no raw camera/audio recording by default. Track frame/turn freshness and discarded epoch counts.
- Dual projection metrics per provider/model/scope: pending/failed/lag, canonical vs indexed hash/count, cross-owner retrieval denial. One branch failure must not disappear behind overall job success.
- Realtime metrics: active sessions, fencing conflicts, first audio, barge-in flush latency, persistence backlog, reserve headroom, video frame rate/age, podcast extension/closing duration. Agent/user IDs not high-cardinality metric labels.
- Runbooks add Xendit missing/duplicate webhook, payment timeout/refund/dispute, unknown usage, low balance, BYOK revocation mid-call, one embedding branch outage, podcast worker recovery and SMTP delivery backlog.
- Retention classes add paper/chunks/audio cache, user facts/assessments, payment/ledger records. Account deletion tombstones private data immediately, revokes credentials/sessions, then deletes both vector spaces and media; legally retained financial rows are minimized/anonymized, not silently erased.
- Production SLOs must include payment credit delay, dual-index convergence, call latency, podcast generation latency, capacity, and DB point-in-time recovery. Define numeric targets in approved deployment policy before release.
