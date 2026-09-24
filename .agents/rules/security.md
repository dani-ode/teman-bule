---
trigger: always_on
description: Security requirements and boundaries
---

- **No Secrets in Code/Logs/Events:** Credentials, API keys, tokens, and connection strings must never appear in source code, logs, event payloads, audit metadata, flow exports, or error messages.
- **Input Validation:** All input boundaries must validate types, ranges, and formats before processing. Treat all user input, LLM output, retrieved documents, and tool arguments as untrusted.
- **Ownership Enforcement:** Resource authorization must be enforced at the application service/repository layer, not just at the route level. Composite ownership (owner + resource) must be verified.
- **SSRF Protection:** All user-supplied URLs must pass HTTPS-only, DNS rebinding protection, private/link-local/metadata IP denial, redirect denial, and egress policy checks.
- **Credential Handling:** BYOK credentials use envelope encryption with key versioning. Plaintext credentials are never returned in API responses, never logged, and never stored in Langflow/queue/event payloads.
- **Auth Token Lifecycle:** Access tokens are short-lived JWT with issuer/audience/kid/expiry validation plus authoritative session-family revocation check. Refresh tokens are opaque, hash-only stored, single-use rotated.
- **Rate Limiting:** Auth endpoints, AI invocations, tool executions, and media uploads must have explicit configurable rate limits. No unbounded operation admission.
